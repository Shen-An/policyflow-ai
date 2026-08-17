import asyncio

import pytest

from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.turn_budget import TurnBudget
from backend.app.core.config import Settings
from backend.app.rag.quality_gate import assess_retrieval_quality
from backend.app.schemas.retrieval import Evidence


def _evidence(text: str = "差旅住宿标准为 500 元") -> Evidence:
    return Evidence(
        knowledge_base_id="kb",
        knowledge_base_name="HR",
        document_id="doc-1",
        document_title="差旅制度",
        snippet=text,
        score=0.9,
        retriever_type="bm25",
        rank=1,
    )


def test_turn_budget_hard_limits_and_snapshot() -> None:
    budget = TurnBudget(max_llm_calls=1, max_retrieval_attempts=1, max_tool_calls=1)
    budget.reserve("llm")
    with pytest.raises(Exception) as exc_info:
        budget.reserve("llm")
    assert getattr(exc_info.value, "code", "") == "TURN_BUDGET_EXHAUSTED"
    assert budget.snapshot()["llm_calls"] == 1


@pytest.mark.asyncio
async def test_turn_budget_wait_for_timeout() -> None:
    budget = TurnBudget(max_total_seconds=0.01)
    with pytest.raises(asyncio.TimeoutError):
        await budget.wait_for(asyncio.sleep(0.2))


@pytest.mark.asyncio
async def test_pipeline_wraps_the_entire_turn_timeout() -> None:
    pipeline = object.__new__(AgentPipeline)
    pipeline.settings = Settings(_env_file=None)

    async def slow_run(*args, **kwargs):
        await asyncio.sleep(0.2)

    pipeline._run_impl = slow_run
    with pytest.raises(Exception) as exc_info:
        await pipeline.run(budget=TurnBudget(max_total_seconds=0.01))
    assert getattr(exc_info.value, "code", "") == "TURN_BUDGET_EXHAUSTED"


def test_quality_gate_retries_rewritten_off_topic_once_then_refuses() -> None:
    unrelated = _evidence("电脑资产领用流程").model_copy(update={"document_title": "IT 资产制度"})
    first = assess_retrieval_quality("差旅住宿标准", [unrelated], rewritten_query="资产", attempt=1)
    second = assess_retrieval_quality("差旅住宿标准", [unrelated], attempt=2)
    assert first["decision"] == "retry"
    assert "RETRIEVAL_LOW_QUALITY" in first["reason_codes"]
    assert second["decision"] == "refuse"


@pytest.mark.asyncio
async def test_compliance_release_decision_refuses_dangling_citation() -> None:
    agent = ComplianceAgent(Settings(CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=True, _env_file=None))
    result = await agent.run("住宿标准 500 元 [9]", [_evidence()])
    assert result.decision == "REFUSE"
    assert result.passed is False


@pytest.mark.asyncio
async def test_compliance_release_decision_requests_revision_for_missing_citation() -> None:
    agent = ComplianceAgent(Settings(CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=True, _env_file=None))
    result = await agent.run("住宿标准按照公司制度执行。", [_evidence()])
    assert result.decision == "REVISE"


@pytest.mark.asyncio
async def test_compliance_requests_revision_for_one_unsupported_number() -> None:
    agent = ComplianceAgent(Settings(CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=True, _env_file=None))
    result = await agent.run("住宿标准为 800 元 [1]。", [_evidence()])
    assert "UNGROUNDED_NUMERIC_CLAIMS" in result.warnings
    assert result.decision == "REVISE"
    assert result.passed is False
