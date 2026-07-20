"""TurnState shared blackboard + centralized error ledger."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.agents.base import PipelineResult, TurnError, TurnState
from backend.app.agents.plan_executor import PlanExecutor
from backend.app.schemas.chat import PlanStep, RouterResult
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalResult


def test_turn_state_record_error_and_blocking() -> None:
    state = TurnState(question="q")
    state.record_error(
        code="NO_RELIABLE_EVIDENCE",
        message="未命中可靠证据",
        source="RetrievalAgent",
        severity="error",
    )
    state.record_error(
        code="OFF_TOPIC",
        message="dropped",
        source="RetrievalAgent",
        severity="warning",
    )
    assert state.has_blocking_errors() is True
    assert len(state.errors) == 2
    assert "OFF_TOPIC" in state.warnings
    result = state.to_pipeline_result()
    assert isinstance(result, PipelineResult)
    assert len(result.errors) == 2
    assert result.turn_state is state
    assert result.status == "completed"


def test_record_step_outcome_maps_statuses() -> None:
    state = TurnState(question="q")
    assert (
        state.record_step_outcome(
            step_id="s1", kind="retrieve", status="success", message="ok"
        )
        is None
    )
    err = state.record_step_outcome(
        step_id="s2", kind="skill", status="error", message="无证据"
    )
    assert err is not None
    assert err.code == "STEP_ERROR_SKILL"
    assert err.severity == "error"
    skip = state.record_step_outcome(
        step_id="s3", kind="tool", status="skipped", message="answer loop"
    )
    assert skip is not None
    assert skip.severity == "info"
    assert skip.code == "STEP_SKIPPED_TOOL"


class _EmptyRetrieval:
    async def run(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(evidence=[], trace=[], latency_ms=1)


class _SkillNoop:
    def suggest(self, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
        return []

    async def execute_one(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "error", "error": "nope"}


@pytest.mark.asyncio
async def test_plan_executor_writes_errors_into_turn_state() -> None:
    executor = PlanExecutor(
        retrieval_agent=_EmptyRetrieval(),  # type: ignore[arg-type]
        skill_agent=_SkillNoop(),  # type: ignore[arg-type]
        parallel_enabled=False,
    )
    steps = [
        PlanStep(id="s1", title="检索", kind="retrieve", query="差旅住宿", status="pending"),
        PlanStep(id="s2", title="回答", kind="answer", status="pending"),
    ]
    router = RouterResult(domain="hr", complexity="multi_step", plan_steps=steps)
    req = RetrievalRequest(query="差旅住宿", knowledge_base_ids=["kb"])
    state = TurnState(question="差旅住宿标准是什么")

    result = await executor.run(
        "差旅住宿标准是什么",
        steps,
        router,
        req,
        knowledge_bases=[type("KB", (), {"id": "kb", "name": "HR", "code": "hr"})()],  # type: ignore[arg-type]
        enable_skill=False,
        execute_skills=False,
        turn_state=state,
    )
    assert any(e.code == "STEP_ERROR_RETRIEVE" for e in result.errors)
    assert any(e.code == "STEP_ERROR_RETRIEVE" for e in state.errors)
    assert state.used_l2 is True
    assert state.retrieval_result is not None
    # answer step should still succeed (marker)
    by_id = {s.id: s for s in result.plan_steps}
    assert by_id["s1"].status == "error"
    assert by_id["s2"].status == "success"


@pytest.mark.asyncio
async def test_plan_executor_no_kb_records_error() -> None:
    executor = PlanExecutor(
        retrieval_agent=_EmptyRetrieval(),  # type: ignore[arg-type]
        skill_agent=_SkillNoop(),  # type: ignore[arg-type]
        parallel_enabled=False,
    )
    steps = [
        PlanStep(id="s1", title="检索", kind="retrieve", query="x", status="pending"),
    ]
    router = RouterResult(domain="hr", complexity="multi_step", plan_steps=steps)
    state = TurnState(question="x")
    result = await executor.run(
        "x",
        steps,
        router,
        None,
        knowledge_bases=[],
        turn_state=state,
    )
    assert any(e.step_id == "s1" and e.severity == "error" for e in result.errors)
    assert any("无可用知识库" in e.message for e in state.errors)


def test_turn_error_model_roundtrip() -> None:
    err = TurnError(
        code="STEP_ERROR_SKILL",
        message="无证据，跳过编造清单",
        source="PlanExecutor",
        step_id="s2",
        severity="error",
        details={"kind": "skill"},
    )
    dumped = err.model_dump(mode="json")
    restored = TurnError.model_validate(dumped)
    assert restored.code == err.code
    assert restored.details["kind"] == "skill"
