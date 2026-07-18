"""Pipeline ToT HITL pause/resume tests (centralized Supervisor, no peer multi-agent)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.agents.base import AnswerResult, PipelineResult
from backend.app.agents.pipeline import AgentPipeline
from backend.app.db.models import KnowledgeBase
from backend.app.schemas.chat import PlanOption, PlanStep, RouterResult
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalStrategy


class _FakeRouter:
    def __init__(self, result: RouterResult) -> None:
        self.result = result
        self.calls = 0

    async def run(self, question: str, knowledge_bases: list[KnowledgeBase]) -> RouterResult:
        self.calls += 1
        return self.result


class _FakeRetrieval:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        from backend.app.agents.base import RetrievalResult

        return RetrievalResult(evidence=[], trace=[], latency_ms=1)


class _FakeAnswer:
    async def run(self, *args: Any, **kwargs: Any) -> AnswerResult:
        return AnswerResult(answer="ok", confidence_score=0.5)


class _FakeSkill:
    async def run(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _FakeCompliance:
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        from backend.app.schemas.chat import ComplianceResult

        return ComplianceResult(passed=True, warnings=[])


def _kb() -> KnowledgeBase:
    return KnowledgeBase(
        id="kb-1",
        code="hr",
        name="HR",
        description="",
        status="active",
        owner_user_id="u1",
    )


def _request() -> RetrievalRequest:
    return RetrievalRequest(
        query="q",
        knowledge_base_ids=["kb-1"],
        strategy=RetrievalStrategy.HYBRID_LIGHTRAG_BM25,
        top_k=5,
        rerank_enabled=False,
        lightrag_query_mode="hybrid",
    )


@pytest.mark.asyncio
async def test_pipeline_pauses_before_retrieve_when_branched_and_hitl(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.agents import plan_branch as plan_branch_mod

    router_result = RouterResult(
        domain="hr",
        task_type="policy_compare",
        need_skill=True,
        complexity="multi_step",
        difficulty="branched",
        reasoning_mode="tot_select",
        plan_steps=[
            PlanStep(id="s1", title="检索", kind="retrieve"),
            PlanStep(id="s2", title="回答", kind="answer"),
        ],
        plan_source="router",
    )
    options = [
        PlanOption(
            id="opt1",
            title="串行",
            recommended=True,
            steps=[
                PlanStep(id="s1", title="检索", kind="retrieve"),
                PlanStep(id="s2", title="回答", kind="answer"),
            ],
        ),
        PlanOption(
            id="opt2",
            title="并行",
            recommended=False,
            steps=[
                PlanStep(id="s1", title="检索A", kind="retrieve"),
                PlanStep(id="s2", title="检索B", kind="retrieve"),
                PlanStep(id="s3", title="回答", kind="answer"),
            ],
        ),
    ]

    async def _fake_generate(*_a: Any, **_k: Any) -> list[PlanOption]:
        return options

    monkeypatch.setattr(plan_branch_mod, "generate_plan_options", _fake_generate)
    monkeypatch.setattr(
        "backend.app.agents.pipeline.generate_plan_options",
        _fake_generate,
    )

    retrieval = _FakeRetrieval()
    pipeline = AgentPipeline(
        router_agent=_FakeRouter(router_result),  # type: ignore[arg-type]
        retrieval_agent=retrieval,  # type: ignore[arg-type]
        skill_agent=_FakeSkill(),  # type: ignore[arg-type]
        answer_agent=_FakeAnswer(),  # type: ignore[arg-type]
        compliance_agent=_FakeCompliance(),  # type: ignore[arg-type]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    result = await pipeline.run(
        "对比一线与二线差旅并给清单",
        [_kb()],
        _request(),
        enable_skill=True,
        hitl=True,
        on_event=on_event,
    )
    assert isinstance(result, PipelineResult)
    assert result.status == "awaiting_plan_selection"
    assert len(result.plan_options) == 2
    assert retrieval.calls == 0
    assert any(name == "plan_options" for name, _ in events)


@pytest.mark.asyncio
async def test_pipeline_resume_executes_selected_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    selected_steps = [
        PlanStep(id="s1", title="检索", kind="retrieve", query="差旅"),
        PlanStep(id="s2", title="回答", kind="answer"),
    ]
    router_result = RouterResult(
        domain="hr",
        task_type="policy_compare",
        need_skill=False,
        complexity="multi_step",
        difficulty="branched",
        reasoning_mode="tot_select",
        plan_steps=selected_steps,
        plan_source="user_selected",
    )
    retrieval = _FakeRetrieval()
    router = _FakeRouter(router_result)
    pipeline = AgentPipeline(
        router_agent=router,  # type: ignore[arg-type]
        retrieval_agent=retrieval,  # type: ignore[arg-type]
        skill_agent=_FakeSkill(),  # type: ignore[arg-type]
        answer_agent=_FakeAnswer(),  # type: ignore[arg-type]
        compliance_agent=_FakeCompliance(),  # type: ignore[arg-type]
    )

    result = await pipeline.run(
        "对比一线与二线差旅并给清单",
        [_kb()],
        _request(),
        enable_skill=True,
        hitl=True,
        selected_plan_steps=selected_steps,
        selected_router_result=router_result,
    )
    assert result.status == "completed"
    assert router.calls == 0  # skip re-route on resume
    assert retrieval.calls >= 1
    assert result.answer_result.answer == "ok"


@pytest.mark.asyncio
async def test_pipeline_hitl_false_auto_picks_recommended(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.agents import plan_branch as plan_branch_mod

    router_result = RouterResult(
        domain="hr",
        task_type="policy_compare",
        need_skill=False,
        complexity="multi_step",
        difficulty="branched",
        reasoning_mode="tot_select",
        plan_steps=[PlanStep(id="s1", title="检索", kind="retrieve")],
        plan_source="router",
    )
    options = [
        PlanOption(
            id="opt1",
            title="推荐路径",
            recommended=True,
            steps=[
                PlanStep(id="s1", title="检索", kind="retrieve", query="差旅"),
                PlanStep(id="s2", title="回答", kind="answer"),
            ],
        ),
        PlanOption(
            id="opt2",
            title="备选",
            recommended=False,
            steps=[PlanStep(id="s1", title="回答", kind="answer")],
        ),
    ]

    async def _fake_generate(*_a: Any, **_k: Any) -> list[PlanOption]:
        return options

    monkeypatch.setattr(plan_branch_mod, "generate_plan_options", _fake_generate)
    monkeypatch.setattr(
        "backend.app.agents.pipeline.generate_plan_options",
        _fake_generate,
    )

    retrieval = _FakeRetrieval()
    pipeline = AgentPipeline(
        router_agent=_FakeRouter(router_result),  # type: ignore[arg-type]
        retrieval_agent=retrieval,  # type: ignore[arg-type]
        skill_agent=_FakeSkill(),  # type: ignore[arg-type]
        answer_agent=_FakeAnswer(),  # type: ignore[arg-type]
        compliance_agent=_FakeCompliance(),  # type: ignore[arg-type]
    )
    result = await pipeline.run(
        "对比一线与二线差旅并给清单",
        [_kb()],
        _request(),
        enable_skill=True,
        hitl=False,
    )
    assert result.status == "completed"
    assert retrieval.calls >= 1
