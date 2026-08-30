import asyncio

import pytest

from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.turn_budget import TurnBudget
from backend.app.core.config import Settings
from backend.app.rag.quality_gate import assess_retrieval_quality, off_topic_reason
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


def _cross_encoder_evidence(score: float, text: str) -> Evidence:
    """Evidence carrying a real cross-encoder logit (the only thresholdable score)."""
    return _evidence(text).model_copy(
        update={
            "rerank_score": score,
            "metadata": {"rerank_method": "cross_encoder", "rerank_score": score},
        }
    )


def test_chat_turn_default_timeout_is_180_seconds() -> None:
    settings = Settings(_env_file=None)

    assert settings.CHAT_TURN_TIMEOUT_SECONDS == 180.0
    assert TurnBudget().max_total_seconds == 180.0


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


def test_quality_gate_blocks_low_cross_encoder_score_even_with_lexical_overlap() -> None:
    """The score gate is stricter than word overlap: shared words, unrelated passage."""
    weak = _cross_encoder_evidence(-12.0, "差旅住宿标准由各部门自行公告")
    quality = assess_retrieval_quality("差旅住宿标准", [weak], attempt=2)

    assert quality["gate"] == "cross_encoder_score"
    assert quality["off_topic"] is True
    assert quality["decision"] == "refuse"
    assert "RETRIEVAL_SCORE_BELOW_THRESHOLD" in quality["reason_codes"]
    assert quality["top_score"] == pytest.approx(-12.0)
    assert quality["lexical_supported"] is True
    assert off_topic_reason(quality).startswith("重排相关度过低")


def test_quality_gate_accepts_high_cross_encoder_score_without_shared_words() -> None:
    """Semantic match with no shared bigrams — the case the lexical gate false-blocks."""
    strong = _cross_encoder_evidence(20.0, "员工出差期间的旅馆费用上限为每晚 500 元")
    quality = assess_retrieval_quality("住宿报销额度是多少", [strong], attempt=2)

    assert quality["gate"] == "cross_encoder_score"
    assert quality["off_topic"] is False
    assert quality["decision"] == "accept"
    assert quality["reason_codes"] == []
    assert quality["lexical_supported"] is False


def test_quality_gate_falls_back_to_lexical_overlap_without_cross_encoder_score() -> None:
    """Default chat has rerank off, so the score gate must not become dead code."""
    fused = _evidence("电脑资产领用流程").model_copy(
        update={
            "document_title": "IT 资产制度",
            "rerank_score": 0.91,
            "metadata": {"rerank_method": "local_lexical_fusion"},
        }
    )
    quality = assess_retrieval_quality("差旅住宿标准", [fused], attempt=2)

    assert quality["gate"] == "lexical_overlap"
    assert quality["top_score"] is None
    assert quality["score_threshold"] is None
    assert quality["off_topic"] is True
    assert "RETRIEVAL_LOW_QUALITY" in quality["reason_codes"]
    assert off_topic_reason(quality).startswith("与原问题词面相关度过低")


def test_quality_gate_score_threshold_comes_from_settings() -> None:
    weak = _cross_encoder_evidence(-12.0, "差旅住宿标准由各部门自行公告")
    lenient = assess_retrieval_quality(
        "差旅住宿标准",
        [weak],
        attempt=2,
        settings=Settings(RETRIEVAL_GATE_MIN_CROSS_ENCODER_SCORE=-20.0, _env_file=None),
    )
    disabled = assess_retrieval_quality(
        "差旅住宿标准",
        [weak],
        attempt=2,
        settings=Settings(RETRIEVAL_GATE_CROSS_ENCODER_ENABLED=False, _env_file=None),
    )

    assert lenient["score_threshold"] == pytest.approx(-20.0)
    assert lenient["off_topic"] is False
    # Disabling the score gate must restore pure lexical behaviour.
    assert disabled["gate"] == "lexical_overlap"
    assert disabled["off_topic"] is False


def test_quality_gate_without_evidence_is_never_off_topic() -> None:
    quality = assess_retrieval_quality("差旅住宿标准", [], attempt=2)

    assert quality["off_topic"] is False
    assert quality["decision"] == "refuse"
    assert "NO_RELIABLE_EVIDENCE" in quality["reason_codes"]


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
