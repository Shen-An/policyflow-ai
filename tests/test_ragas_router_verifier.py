"""Unit tests for router wiring, verifier rules, and optional RAGAS proxy."""

from __future__ import annotations

import pytest

from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeBase
from backend.app.evals.ragas_runner import RagasEvaluationInput, RagasRunner
from backend.app.schemas.chat import RouterResult
from backend.app.schemas.retrieval import Evidence


@pytest.mark.asyncio
async def test_heuristic_router_sets_need_skill_and_task_type() -> None:
    router = RouterAgent(llm_service=None)
    kb = KnowledgeBase(
        id="kb1",
        name="HR",
        code="hr",
        department_id="d1",
        rag_workspace="rag/hr",
    )
    result = await router.run("差旅报销流程有哪些步骤？", [kb])
    assert result.domain == "hr"
    assert result.task_type == "process_checklist"
    assert result.need_skill is True
    assert "skill.run" in result.tool_hints


def test_skill_agent_respects_need_skill_false_for_plain_qa() -> None:
    agent = SkillAgent()
    router = RouterResult(
        domain="hr",
        task_type="knowledge_qa",
        risk_level="low",
        need_skill=False,
    )
    assert agent.suggest("住宿标准是多少？", router, enabled=True) == []


def test_skill_agent_uses_router_task_type() -> None:
    agent = SkillAgent()
    router = RouterResult(
        domain="hr",
        task_type="process_checklist",
        risk_level="low",
        need_skill=True,
    )
    suggestions = agent.suggest("随便问一句", router, enabled=True)
    assert suggestions and suggestions[0]["name"] == "process_checklist"


@pytest.mark.asyncio
async def test_verifier_hard_refuse_without_evidence() -> None:
    agent = ComplianceAgent(Settings(CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=True, _env_file=None))
    result = await agent.run("我觉得可以报销一千元。", [])
    assert result.passed is False
    assert "NO_RELIABLE_EVIDENCE" in result.warnings
    assert "SOFT_ANSWER_WITHOUT_EVIDENCE" in result.warnings


@pytest.mark.asyncio
async def test_verifier_detects_dangling_citation() -> None:
    agent = ComplianceAgent(Settings(CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=True, _env_file=None))
    evidence = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="d1",
            document_title="Travel",
            snippet="住宿标准 500 元",
            score=0.9,
            retriever_type="bm25",
            rank=1,
        )
    ]
    result = await agent.run("根据制度住宿标准是 500 元 [9]。", evidence)
    assert "DANGLING_CITATIONS" in result.warnings
    # Soft warning should not fail by itself.
    assert result.passed is True


@pytest.mark.asyncio
async def test_ragas_proxy_fallback_when_dependency_missing() -> None:
    runner = RagasRunner(allow_proxy_fallback=True)
    result = await runner.run(
        RagasEvaluationInput(
            question="住宿标准是多少？",
            answer="住宿标准是 500 元，见制度。",
            contexts=["差旅制度：住宿标准 500 元。"],
            reference_answer="住宿标准 500 元",
        ),
        enabled=True,
    )
    assert result.status == "completed"
    assert result.metrics_source == "token_overlap_proxy"
    assert "faithfulness" in result.metrics
    assert result.metrics["faithfulness"] > 0


@pytest.mark.asyncio
async def test_ragas_disabled_is_skipped() -> None:
    runner = RagasRunner()
    result = await runner.run(
        RagasEvaluationInput(question="q", answer="a", contexts=["c"]),
        enabled=False,
    )
    assert result.status == "skipped"
    assert result.reason == "disabled"


def test_estimate_confidence_uses_citations_and_penalties() -> None:
    from backend.app.agents.grounding import estimate_answer_confidence

    evidence = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="d1",
            document_title="差旅",
            snippet="住宿标准 500 元，需提前申请。",
            score=0.9,
            retriever_type="bm25",
            rank=1,
            metadata={"score_is_synthetic": False},
        )
    ]
    good = estimate_answer_confidence(
        answer="根据制度，住宿标准是 500 元，需提前申请 [1]。",
        evidence=evidence,
    )
    missing = estimate_answer_confidence(
        answer="根据制度可以随便报销很多钱。",
        evidence=evidence,
        compliance_warnings=["MISSING_CITATION_MARKERS", "UNGROUNDED_NUMERIC_CLAIMS"],
    )
    assert good > missing
    assert good >= 0.5
    assert missing < good


def test_resolve_allowed_tools_from_router_hints() -> None:
    from backend.app.tools.chat_tools import resolve_allowed_tools

    assert resolve_allowed_tools(None) >= {"kb.search", "skill.run", "draft.create"}
    assert resolve_allowed_tools([]) == resolve_allowed_tools(None)
    narrowed = resolve_allowed_tools(["kb.search", "draft"], need_skill=True)
    assert narrowed == {"kb.search", "draft.create", "skill.run"}
    # Unknown-only hints fall back to full default set.
    fallback = resolve_allowed_tools(["totally-unknown-tool"])
    assert "kb.search" in fallback and "mcp.call" in fallback


def test_claim_evidence_flags_weak_sentences() -> None:
    from backend.app.agents.grounding import claim_evidence_analysis

    evidence = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="d1",
            snippet="差旅需要部门负责人审批。",
            score=0.8,
            retriever_type="bm25",
            rank=1,
        )
    ]
    result = claim_evidence_analysis(
        "火星基地津贴按银河币结算。星际航班可以免费升舱办理。",
        evidence,
        min_overlap=2,
        weak_ratio_threshold=0.3,
    )
    assert result["sentence_count"] >= 1
    assert result["weak_sentence_count"] >= 1
    assert result["flag"] is True
