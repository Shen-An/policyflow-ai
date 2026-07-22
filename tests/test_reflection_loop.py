"""Tests for the Critique → Improve reflection closed-loop."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.agents.answer_agent import HARD_REFUSE_ANSWER, AnswerAgent
from backend.app.agents.base import AnswerResult, TurnState
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.critique_agent import CritiqueAgent, normalize_critique_result
from backend.app.agents.improve_agent import ImproveAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.reflection_loop import ReflectionLoop, should_run_reflection
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeBase
from backend.app.schemas.chat import RouterResult
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalResult


def _settings(**kwargs: Any) -> Settings:
    base = {
        "CHAT_REFLECTION_ENABLED": True,
        "CHAT_REFLECTION_MAX_ROUNDS": 2,
        "CHAT_REFLECTION_IN_EVAL": False,
        "CHAT_REFLECTION_CONFIDENCE_THRESHOLD": 0.72,
        "CHAT_REFLECTION_PASS_MAX_WARNINGS": 1,
        "CHAT_HARD_REFUSE_WITHOUT_EVIDENCE": True,
        "CHAT_PLANNING_ENABLED": False,
        "CHAT_TOT_ENABLED": False,
        "CHAT_PLAN_EXECUTOR": False,
        "_env_file": None,
    }
    base.update(kwargs)
    return Settings(**base)


def _evidence(snippet: str = "差旅住宿标准为每晚 500 元。", rank: int = 1) -> Evidence:
    return Evidence(
        rank=rank,
        document_id="d1",
        document_title="差旅管理办法",
        chunk_id="c1",
        knowledge_base_id="kb1",
        knowledge_base_name="HR",
        snippet=snippet,
        score=0.9,
        retriever_type="hybrid",
    )


class ScriptedLLM:
    """Queue of complete() responses; records calls."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            return ""
        return self.responses.pop(0)


# ---------- should_run_reflection ----------


def test_skip_when_disabled() -> None:
    ok, reason = should_run_reflection(
        settings=_settings(CHAT_REFLECTION_ENABLED=False),
        allow_reflection=True,
        answer="有证据的回答 [1]",
        evidence=[_evidence()],
        confidence=0.5,
        router_result=RouterResult(domain="hr", difficulty="multi_step", complexity="multi_step"),
        skill_results=None,
    )
    assert ok is False
    assert reason == "disabled"


def test_skip_hard_refuse_and_no_evidence() -> None:
    settings = _settings()
    ok, reason = should_run_reflection(
        settings=settings,
        allow_reflection=True,
        answer=HARD_REFUSE_ANSWER,
        evidence=[],
        confidence=0.0,
        router_result=RouterResult(domain="hr", difficulty="multi_step"),
        skill_results=None,
    )
    assert ok is False
    assert reason == "hard_refuse"

    ok2, reason2 = should_run_reflection(
        settings=settings,
        allow_reflection=True,
        answer="正常回答",
        evidence=[],
        confidence=0.5,
        router_result=RouterResult(domain="hr", risk_level="high"),
        skill_results=None,
    )
    assert ok2 is False
    assert reason2 == "hard_refuse"


def test_skip_allow_reflection_false() -> None:
    ok, reason = should_run_reflection(
        settings=_settings(),
        allow_reflection=False,
        answer="回答 [1]",
        evidence=[_evidence()],
        confidence=0.4,
        router_result=RouterResult(domain="hr", difficulty="multi_step"),
        skill_results=None,
    )
    assert ok is False
    assert reason == "allow_reflection_false"


def test_skip_simple_high_confidence() -> None:
    ok, reason = should_run_reflection(
        settings=_settings(),
        allow_reflection=True,
        answer="住宿标准 500 元 [1]",
        evidence=[_evidence()],
        confidence=0.9,
        router_result=RouterResult(
            domain="hr",
            difficulty="simple",
            complexity="simple",
            reasoning_mode="cot_direct",
            risk_level="low",
        ),
        skill_results=None,
    )
    assert ok is False
    assert reason == "not_high_stakes"


def test_trigger_multi_step_and_skill_and_low_confidence() -> None:
    settings = _settings()
    ok, reason = should_run_reflection(
        settings=settings,
        allow_reflection=True,
        answer="清单 [1]",
        evidence=[_evidence()],
        confidence=0.9,
        router_result=RouterResult(
            domain="hr",
            difficulty="multi_step",
            complexity="multi_step",
            reasoning_mode="cot_steps",
        ),
        skill_results=None,
    )
    assert ok is True
    assert "multi_step" in reason

    ok2, reason2 = should_run_reflection(
        settings=settings,
        allow_reflection=True,
        answer="对比 [1]",
        evidence=[_evidence()],
        confidence=0.9,
        router_result=RouterResult(domain="hr", difficulty="simple", risk_level="low"),
        skill_results=[{"name": "process_checklist", "status": "success", "output": {}}],
    )
    assert ok2 is True
    assert "skill_success" in reason2

    ok3, reason3 = should_run_reflection(
        settings=settings,
        allow_reflection=True,
        answer="弱回答 [1]",
        evidence=[_evidence()],
        confidence=0.4,
        router_result=RouterResult(domain="hr", difficulty="simple", risk_level="low"),
        skill_results=None,
    )
    assert ok3 is True
    assert "low_confidence" in reason3


# ---------- normalize_critique_result / PASS exit ----------


def test_normalize_empty_issues_is_pass() -> None:
    result = normalize_critique_result({"verdict": "NEEDS_IMPROVEMENT", "issues": []})
    assert result is not None
    assert result.verdict == "PASS"


def test_normalize_warning_budget_pass() -> None:
    result = normalize_critique_result(
        {
            "verdict": "NEEDS_IMPROVEMENT",
            "issues": [
                {
                    "id": "I1",
                    "dimension": "structure_clarity",
                    "severity": "warning",
                    "problem": "可以更分点",
                }
            ],
        },
        pass_max_warnings=1,
    )
    assert result is not None
    assert result.verdict == "PASS"


def test_normalize_error_needs_improvement() -> None:
    result = normalize_critique_result(
        {
            "verdict": "PASS",
            "issues": [
                {
                    "id": "I1",
                    "dimension": "numeric_fidelity",
                    "severity": "error",
                    "problem": "金额与证据不符",
                }
            ],
        }
    )
    assert result is not None
    assert result.verdict == "NEEDS_IMPROVEMENT"


# ---------- ReflectionLoop bounds ----------


@pytest.mark.asyncio
async def test_pass_never_calls_improve() -> None:
    llm = ScriptedLLM(
        [
            '{"verdict":"PASS","summary":"ok","issues":[]}',
        ]
    )
    loop = ReflectionLoop(
        CritiqueAgent(llm, _settings()),
        ImproveAgent(llm, _settings()),
        _settings(),
    )
    draft = AnswerResult(answer="住宿标准为每晚 500 元 [1]", confidence_score=0.5)
    state = TurnState(question="住宿标准？")
    out, reflection = await loop.run(
        question="住宿标准？",
        answer_result=draft,
        evidence=[_evidence()],
        router_result=RouterResult(
            domain="hr", difficulty="multi_step", complexity="multi_step"
        ),
        turn_state=state,
        allow_reflection=True,
    )
    assert reflection.triggered is True
    assert reflection.stopped_reason == "pass"
    assert out.answer == draft.answer
    # Only critique call, no improve.
    assert len(llm.calls) == 1
    assert "评审员" in llm.calls[0][0] or "critic" in llm.calls[0][0].lower() or "检查维度" in llm.calls[0][0]


@pytest.mark.asyncio
async def test_max_rounds_two_no_third_improve() -> None:
    # Round1 critique NEEDS → improve → Round2 critique NEEDS → stop (no 2nd improve)
    needs = (
        '{"verdict":"NEEDS_IMPROVEMENT","summary":"bad",'
        '"issues":[{"id":"I1","dimension":"numeric_fidelity","severity":"error",'
        '"problem":"金额错误","fix_hint":"改成证据中的 500 元"}]}'
    )
    llm = ScriptedLLM(
        [
            needs,
            "修订后：住宿标准为每晚 500 元 [1]",
            needs,
            "不应该被调用的第三次改写",
        ]
    )
    settings = _settings(CHAT_REFLECTION_MAX_ROUNDS=2)
    loop = ReflectionLoop(
        CritiqueAgent(llm, settings),
        ImproveAgent(llm, settings),
        settings,
    )
    draft = AnswerResult(answer="住宿标准 800 元", confidence_score=0.5)
    out, reflection = await loop.run(
        question="住宿标准？",
        answer_result=draft,
        evidence=[_evidence()],
        router_result=RouterResult(
            domain="hr", difficulty="multi_step", complexity="multi_step"
        ),
        turn_state=TurnState(question="住宿标准？"),
        allow_reflection=True,
    )
    assert reflection.triggered is True
    assert reflection.stopped_reason == "max_rounds"
    assert out.answer == "修订后：住宿标准为每晚 500 元 [1]"
    # 2 critiques + 1 improve = 3 calls; no 4th
    assert len(llm.calls) == 3
    assert len(reflection.rounds) == 2
    assert reflection.rounds[0].improved is True
    assert reflection.rounds[1].improved is False


@pytest.mark.asyncio
async def test_parse_error_fail_open_keeps_draft() -> None:
    llm = ScriptedLLM(["not-json-at-all {{{"])
    settings = _settings()
    loop = ReflectionLoop(
        CritiqueAgent(llm, settings),
        ImproveAgent(llm, settings),
        settings,
    )
    draft = AnswerResult(answer="原始草稿 [1]", confidence_score=0.4)
    state = TurnState(question="q")
    out, reflection = await loop.run(
        question="q",
        answer_result=draft,
        evidence=[_evidence()],
        router_result=RouterResult(domain="hr", risk_level="high"),
        turn_state=state,
        allow_reflection=True,
    )
    assert out.answer == "原始草稿 [1]"
    assert reflection.stopped_reason == "parse_error"
    assert any(e.code == "REFLECTION_CRITIQUE_PARSE_ERROR" for e in state.errors)


@pytest.mark.asyncio
async def test_hard_refuse_skips_without_llm_call() -> None:
    llm = ScriptedLLM(['{"verdict":"PASS","issues":[]}'])
    settings = _settings()
    loop = ReflectionLoop(
        CritiqueAgent(llm, settings),
        ImproveAgent(llm, settings),
        settings,
    )
    draft = AnswerResult(answer=HARD_REFUSE_ANSWER, confidence_score=0.0)
    out, reflection = await loop.run(
        question="乱问",
        answer_result=draft,
        evidence=[],
        router_result=RouterResult(domain="hr", difficulty="multi_step"),
        turn_state=TurnState(question="乱问"),
        allow_reflection=True,
    )
    assert reflection.triggered is False
    assert reflection.stopped_reason == "hard_refuse"
    assert out.answer == HARD_REFUSE_ANSWER
    assert llm.calls == []


# ---------- Pipeline integration ----------


class FixedAnswerAgent(AnswerAgent):
    def __init__(self, answer: str, confidence: float = 0.5) -> None:
        self._answer = answer
        self._confidence = confidence
        self.settings = _settings()

    async def run(self, question, evidence, working_set=None, **kwargs):  # type: ignore[no-untyped-def]
        if not evidence:
            return AnswerResult(answer=HARD_REFUSE_ANSWER, confidence_score=0.0)
        return AnswerResult(answer=self._answer, confidence_score=self._confidence)


class FixedRetrievalAgent:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.evidence = evidence

    async def run(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(evidence=self.evidence, trace=[], latency_ms=1)


class FixedRouterAgent:
    def __init__(self, result: RouterResult) -> None:
        self.result = result

    async def run(self, question: str, knowledge_bases: list[KnowledgeBase]) -> RouterResult:
        return self.result


@pytest.mark.asyncio
async def test_pipeline_compliance_sees_improved_answer() -> None:
    needs = (
        '{"verdict":"NEEDS_IMPROVEMENT","summary":"缺引用",'
        '"issues":[{"id":"I1","dimension":"citation_integrity","severity":"error",'
        '"problem":"缺 [n] 引用","fix_hint":"补上 [1]"}]}'
    )
    improved = "差旅住宿标准为每晚 500 元 [1]。"
    llm = ScriptedLLM([needs, improved, '{"verdict":"PASS","summary":"ok","issues":[]}'])
    settings = _settings(CHAT_REFLECTION_MAX_ROUNDS=2)
    loop = ReflectionLoop(
        CritiqueAgent(llm, settings),
        ImproveAgent(llm, settings),
        settings,
    )
    pipeline = AgentPipeline(
        FixedRouterAgent(  # type: ignore[arg-type]
            RouterResult(
                domain="hr",
                difficulty="multi_step",
                complexity="multi_step",
                reasoning_mode="cot_steps",
                risk_level="medium",
            )
        ),
        FixedRetrievalAgent([_evidence()]),  # type: ignore[arg-type]
        FixedAnswerAgent("差旅住宿标准为每晚 500 元。", confidence=0.5),
        SkillAgent(),
        ComplianceAgent(settings),
        settings,
        reflection_loop=loop,
    )
    kb = KnowledgeBase(
        id="kb1", name="HR", code="hr", department_id="d1", rag_workspace="rag/hr"
    )
    result = await pipeline.run(
        "住宿标准是多少？",
        [kb],
        RetrievalRequest(
            query="住宿标准",
            knowledge_base_ids=["kb1"],
            top_k=5,
        ),
        allow_reflection=True,
    )
    assert result.answer_result.answer == improved
    assert result.reflection is not None
    assert result.reflection.triggered is True
    assert result.compliance.passed is True


@pytest.mark.asyncio
async def test_pipeline_without_reflection_loop_unchanged() -> None:
    settings = _settings()
    pipeline = AgentPipeline(
        FixedRouterAgent(  # type: ignore[arg-type]
            RouterResult(domain="hr", difficulty="simple", risk_level="low")
        ),
        FixedRetrievalAgent([_evidence()]),  # type: ignore[arg-type]
        FixedAnswerAgent("住宿标准 500 元 [1]", confidence=0.9),
        SkillAgent(),
        ComplianceAgent(settings),
        settings,
        reflection_loop=None,
    )
    kb = KnowledgeBase(
        id="kb1", name="HR", code="hr", department_id="d1", rag_workspace="rag/hr"
    )
    result = await pipeline.run(
        "住宿标准？",
        [kb],
        RetrievalRequest(query="住宿", knowledge_base_ids=["kb1"], top_k=3),
    )
    assert result.answer_result.answer == "住宿标准 500 元 [1]"
    assert result.reflection is None


@pytest.mark.asyncio
async def test_pipeline_hard_refuse_no_reflection() -> None:
    llm = ScriptedLLM(['{"verdict":"PASS","issues":[]}'])
    settings = _settings()
    loop = ReflectionLoop(
        CritiqueAgent(llm, settings),
        ImproveAgent(llm, settings),
        settings,
    )
    pipeline = AgentPipeline(
        FixedRouterAgent(  # type: ignore[arg-type]
            RouterResult(
                domain="hr",
                difficulty="multi_step",
                complexity="multi_step",
            )
        ),
        FixedRetrievalAgent([]),  # type: ignore[arg-type]
        FixedAnswerAgent("unused"),
        SkillAgent(),
        ComplianceAgent(settings),
        settings,
        reflection_loop=loop,
    )
    kb = KnowledgeBase(
        id="kb1", name="HR", code="hr", department_id="d1", rag_workspace="rag/hr"
    )
    result = await pipeline.run(
        "完全无关的胡话",
        [kb],
        RetrievalRequest(query="胡话", knowledge_base_ids=["kb1"], top_k=3),
        allow_reflection=True,
    )
    assert result.answer_result.answer == HARD_REFUSE_ANSWER
    assert result.reflection is not None
    assert result.reflection.triggered is False
    assert result.reflection.stopped_reason == "hard_refuse"
    assert llm.calls == []
