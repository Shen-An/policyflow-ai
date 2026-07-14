"""Tests for multi-doc retrieval metrics and skill→answer context wiring."""

from __future__ import annotations

import pytest

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.base import AnswerResult
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeBase
from backend.app.evals.retrieval_metrics import calculate_retrieval_metrics
from backend.app.rag.protocols import LLMCompletion, LLMMessage
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalResult, RetrievalStrategy
from backend.app.services.eval_dataset_import import _news_fields


class CaptureLLM:
    def __init__(self) -> None:
        self.last_user = ""

    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.last_user = user_prompt
        return "根据 Skill 与证据：步骤一…… [1]"

    async def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict],
    ) -> LLMCompletion:
        user_bits = [m.content or "" for m in messages if m.role == "user"]
        self.last_user = "\n".join(user_bits)
        return LLMCompletion(content="根据 Skill 与证据：步骤一…… [1]", tool_calls=[])


class FakeRetriever:
    async def run(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(
            evidence=[
                Evidence(
                    knowledge_base_id="kb",
                    knowledge_base_name="HR",
                    document_id="d1",
                    document_title="差旅制度",
                    snippet="出差需提前申请，住宿按标准报销。",
                    score=0.9,
                    retriever_type="bm25",
                    rank=1,
                )
            ],
            trace=[],
            latency_ms=1,
        )


def test_multi_doc_hit_all_and_doc_recall() -> None:
    retrieved = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="a",
            snippet="a",
            retriever_type="bm25",
            rank=1,
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="x",
            snippet="x",
            retriever_type="bm25",
            rank=2,
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="b",
            snippet="b",
            retriever_type="bm25",
            rank=3,
        ),
    ]
    metrics = calculate_retrieval_metrics(retrieved, ["a", "b", "c"], [], [1, 3, 5])
    assert metrics["multi_doc"] is True
    assert metrics["gold_doc_count"] == 3
    assert metrics["hit_at_1"] == 1.0
    assert metrics["hit_all_at_1"] == 0.0
    assert metrics["hit_all_at_3"] == 0.0  # missing c
    assert metrics["doc_recall_at_3"] == pytest.approx(2 / 3)
    assert metrics["hit_all_at_5"] == 0.0


def test_news_fields_multidoc_ids() -> None:
    item = {
        "ID": "evt1",
        "event": "测试事件",
        "news1": "正文1",
        "news2": "正文2",
        "news3": "正文3",
        "questions": "q",
        "answers": "a",
    }
    docs = _news_fields(item, "questanswer_3docs")
    assert len(docs) == 3
    assert docs[0][0] == "evt1#1"
    assert docs[1][0] == "evt1#2"
    assert docs[2][0] == "evt1#3"


@pytest.mark.asyncio
async def test_answer_agent_includes_skill_results_in_prompt() -> None:
    llm = CaptureLLM()
    agent = AnswerAgent(llm, Settings(CHAT_TOOLS_ENABLED=False, _env_file=None))
    evidence = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="d1",
            document_title="差旅",
            snippet="需提前申请",
            score=0.9,
            retriever_type="bm25",
            rank=1,
        )
    ]
    skill_results = [
        {
            "name": "process_checklist",
            "status": "success",
            "output": {
                "title": "差旅流程",
                "steps": [{"order": 1, "action": "提交申请", "evidence_refs": [1]}],
            },
        }
    ]
    result = await agent.run(
        "差旅流程？",
        evidence,
        skill_results=skill_results,
    )
    assert isinstance(result, AnswerResult)
    assert "已执行 Skill 结构化结果" in llm.last_user
    assert "process_checklist" in llm.last_user
    assert "提交申请" in llm.last_user


@pytest.mark.asyncio
async def test_pipeline_runs_skill_before_answer_and_feeds_context() -> None:
    llm = CaptureLLM()
    settings = Settings(
        CHAT_TOOLS_ENABLED=False,
        CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=True,
        _env_file=None,
    )

    class StubSkillAgent(SkillAgent):
        def __init__(self) -> None:
            # Non-None registry so pipeline's should_execute gate opens.
            super().__init__(skill_registry=object())  # type: ignore[arg-type]

        async def execute_suggested(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return (
                [{"name": "process_checklist", "description": "清单"}],
                [
                    {
                        "name": "process_checklist",
                        "status": "success",
                        "output": {"steps": [{"order": 1, "action": "先审批"}]},
                    }
                ],
            )

        async def run(self, question, router_result, enabled):  # type: ignore[no-untyped-def]
            return [{"name": "process_checklist", "description": "清单"}]

    class NeedSkillRouter(RouterAgent):
        async def run(self, question, knowledge_bases):  # type: ignore[no-untyped-def]
            from backend.app.schemas.chat import RouterResult

            return RouterResult(
                domain="hr",
                task_type="process_checklist",
                risk_level="low",
                need_skill=True,
            )

    pipeline = AgentPipeline(
        NeedSkillRouter(None),
        FakeRetriever(),  # type: ignore[arg-type]
        AnswerAgent(llm, settings),
        StubSkillAgent(),
        ComplianceAgent(settings),
    )
    kb = KnowledgeBase(
        id="kb",
        name="HR",
        code="hr",
        department_id="d",
        rag_workspace="rag/hr",
    )
    result = await pipeline.run(
        "差旅申请流程？",
        [kb],
        RetrievalRequest(
            query="差旅申请流程？",
            knowledge_base_ids=["kb"],
            strategy=RetrievalStrategy.BM25_ONLY,
        ),
        enable_skill=True,
        execute_skills=True,
        session=object(),  # type: ignore[arg-type]
        user=object(),  # type: ignore[arg-type]
    )
    assert result.skill_results
    assert "已执行 Skill 结构化结果" in llm.last_user
    assert "先审批" in llm.last_user
