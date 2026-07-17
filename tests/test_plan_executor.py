"""Unit tests for L2 PlanExecutor dependency waves and parallel scheduling."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.agents.plan_executor import (
    PlanExecutor,
    build_execution_waves,
    infer_dependencies,
    merge_evidence,
    should_use_plan_executor,
)
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.schemas.chat import PlanStep, RouterResult
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalResult


def _step(
    sid: str,
    kind: str,
    *,
    query: str | None = None,
    skill_hint: str | None = None,
    depends_on: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        id=sid,
        title=sid,
        kind=kind,  # type: ignore[arg-type]
        query=query,
        skill_hint=skill_hint,
        depends_on=depends_on or [],
    )


def test_infer_independent_retrieves_have_no_cross_deps() -> None:
    steps = [
        _step("s1", "retrieve", query="一线住宿标准"),
        _step("s2", "retrieve", query="二线住宿标准"),
        _step("s3", "skill", skill_hint="policy_compare"),
        _step("s4", "answer"),
    ]
    inferred = infer_dependencies(steps)
    by_id = {s.id: s for s in inferred}
    assert by_id["s1"].depends_on == []
    assert by_id["s2"].depends_on == []
    assert set(by_id["s3"].depends_on) == {"s1", "s2"}
    assert "s3" in by_id["s4"].depends_on or set(by_id["s4"].depends_on) >= {"s1", "s2", "s3"}


def test_same_query_retrieves_serialize() -> None:
    steps = [
        _step("s1", "retrieve", query="差旅标准"),
        _step("s2", "retrieve", query="差旅标准"),
        _step("s3", "answer"),
    ]
    inferred = infer_dependencies(steps)
    by_id = {s.id: s for s in inferred}
    assert by_id["s2"].depends_on == ["s1"]


def test_parallel_wave_for_independent_retrieves() -> None:
    steps = infer_dependencies(
        [
            _step("s1", "retrieve", query="A"),
            _step("s2", "retrieve", query="B"),
            _step("s3", "skill", skill_hint="process_checklist"),
            _step("s4", "answer"),
        ]
    )
    waves = build_execution_waves(steps, parallel_enabled=True)
    assert ["s1", "s2"] in waves or waves[0] == ["s1", "s2"]
    # skill and answer are later singleton-ish waves
    flat = [sid for wave in waves for sid in wave]
    assert flat.index("s1") < flat.index("s3")
    assert flat.index("s2") < flat.index("s3")
    assert flat.index("s3") < flat.index("s4")


def test_parallel_disabled_serializes_ready_steps() -> None:
    steps = infer_dependencies(
        [
            _step("s1", "retrieve", query="A"),
            _step("s2", "retrieve", query="B"),
        ]
    )
    waves = build_execution_waves(steps, parallel_enabled=False)
    assert waves == [["s1"], ["s2"]]


def test_should_use_plan_executor_gate() -> None:
    assert should_use_plan_executor([], enabled=True) is False
    assert (
        should_use_plan_executor(
            [_step("s1", "retrieve"), _step("s2", "answer")],
            enabled=True,
        )
        is False
    )
    assert (
        should_use_plan_executor(
            [
                _step("s1", "retrieve", query="a"),
                _step("s2", "skill", skill_hint="process_checklist"),
                _step("s3", "answer"),
            ],
            enabled=True,
        )
        is True
    )
    assert (
        should_use_plan_executor(
            [
                _step("s1", "retrieve", query="a"),
                _step("s2", "retrieve", query="b"),
            ],
            enabled=False,
        )
        is False
    )


def test_merge_evidence_dedups() -> None:
    a = Evidence(
        knowledge_base_id="kb",
        knowledge_base_name="HR",
        document_id="d1",
        document_title="T",
        chunk_id="c1",
        snippet="住宿 500",
        score=0.9,
        retriever_type="bm25",
        rank=1,
    )
    b = Evidence(
        knowledge_base_id="kb",
        knowledge_base_name="HR",
        document_id="d1",
        document_title="T",
        chunk_id="c1",
        snippet="住宿 500",
        score=0.8,
        retriever_type="lightrag",
        rank=1,
    )
    c = Evidence(
        knowledge_base_id="kb",
        knowledge_base_name="HR",
        document_id="d2",
        document_title="T2",
        chunk_id="c2",
        snippet="餐补 100",
        score=0.7,
        retriever_type="bm25",
        rank=1,
    )
    merged = merge_evidence([a], [b, c])
    assert len(merged) == 2
    assert {m.chunk_id for m in merged} == {"c1", "c2"}


class _FakeRetrieval:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self._lock = asyncio.Lock()
        self.concurrent = 0
        self.max_concurrent = 0

    async def run(self, request: RetrievalRequest) -> RetrievalResult:
        async with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.queries.append(request.query)
        await asyncio.sleep(0.05)
        async with self._lock:
            self.concurrent -= 1
        return RetrievalResult(
            evidence=[
                Evidence(
                    knowledge_base_id="kb",
                    knowledge_base_name="HR",
                    document_id=f"d-{request.query}",
                    document_title=request.query,
                    chunk_id=f"c-{request.query}",
                    # Include question tokens so off-topic gate does not drop hits in unit tests.
                    snippet=f"差旅 住宿 标准 报销 {request.query} 制度条款",
                    score=0.9,
                    retriever_type="bm25",
                    rank=1,
                )
            ],
            trace=[],
            latency_ms=10,
        )


@pytest.mark.asyncio
async def test_plan_executor_runs_independent_retrieves_in_parallel() -> None:
    fake = _FakeRetrieval()
    executor = PlanExecutor(
        retrieval_agent=fake,  # type: ignore[arg-type]
        skill_agent=SkillAgent(),
        parallel_enabled=True,
    )
    steps = [
        _step("s1", "retrieve", query="一线差旅住宿"),
        _step("s2", "retrieve", query="二线差旅住宿"),
        _step("s3", "answer"),
    ]
    router = RouterResult(domain="hr", complexity="multi_step", plan_steps=steps)
    req = RetrievalRequest(query="fallback", knowledge_base_ids=["kb"])
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    # Minimal KB-like object not required by fake retrieval.
    result = await executor.run(
        "对比一线与二线差旅住宿标准",
        steps,
        router,
        req,
        knowledge_bases=[type("KB", (), {"id": "kb", "name": "HR", "code": "hr"})()],  # type: ignore[arg-type]
        enable_skill=False,
        execute_skills=False,
        on_event=on_event,
    )
    assert result.parallel_used is True
    assert fake.max_concurrent >= 2
    assert set(fake.queries) == {"一线差旅住宿", "二线差旅住宿"}
    assert len(result.evidence) == 2
    # plan_step events emitted for both retrieves
    step_events = [p for e, p in events if e == "plan_step"]
    assert any(p.get("id") == "s1" and p.get("status") == "success" for p in step_events)
    assert any(p.get("id") == "s2" and p.get("status") == "success" for p in step_events)
