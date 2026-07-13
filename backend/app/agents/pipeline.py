"""Router, retrieval, answer, Skill, and compliance orchestration."""

from collections.abc import Awaitable, Callable

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.base import MemoryWorkingSet, PipelineResult
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.db.models import KnowledgeBase
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalResult

StageCallback = Callable[[str, str, str], Awaitable[None] | None]


class AgentPipeline:
    def __init__(
        self,
        router_agent: RouterAgent,
        retrieval_agent: RetrievalAgent,
        answer_agent: AnswerAgent,
        skill_agent: SkillAgent,
        compliance_agent: ComplianceAgent,
    ) -> None:
        self.router_agent = router_agent
        self.retrieval_agent = retrieval_agent
        self.answer_agent = answer_agent
        self.skill_agent = skill_agent
        self.compliance_agent = compliance_agent

    async def _emit(
        self,
        on_stage: StageCallback | None,
        stage: str,
        status: str,
        message: str,
    ) -> None:
        if on_stage is None:
            return
        result = on_stage(stage, status, message)
        if result is not None:
            await result

    async def run(
        self,
        question: str,
        knowledge_bases: list[KnowledgeBase],
        retrieval_request: RetrievalRequest | None,
        enable_skill: bool = True,
        working_set: MemoryWorkingSet | None = None,
        on_stage: StageCallback | None = None,
    ) -> PipelineResult:
        await self._emit(on_stage, "RouterAgent", "running", "正在分析问题领域与风险…")
        router_result = await self.router_agent.run(question, knowledge_bases)
        await self._emit(
            on_stage,
            "RouterAgent",
            "success",
            f"domain={router_result.domain}, risk={router_result.risk_level}",
        )

        await self._emit(on_stage, "RetrievalAgent", "running", "正在检索授权知识库…")
        retrieval_result = (
            await self.retrieval_agent.run(retrieval_request)
            if retrieval_request is not None
            else RetrievalResult(evidence=[], trace=[], latency_ms=0)
        )
        evidence_count = len(retrieval_result.evidence)
        await self._emit(
            on_stage,
            "RetrievalAgent",
            "success" if evidence_count else "empty",
            f"命中 {evidence_count} 条证据",
        )

        await self._emit(on_stage, "AnswerAgent", "running", "正在生成回答…")
        answer_result = await self.answer_agent.run(
            question,
            retrieval_result.evidence,
            working_set,
        )
        await self._emit(on_stage, "AnswerAgent", "success", "回答已生成")

        await self._emit(on_stage, "SkillAgent", "running", "正在匹配可用 Skill…")
        suggested_skills = await self.skill_agent.run(question, router_result, enable_skill)
        await self._emit(
            on_stage,
            "SkillAgent",
            "success" if suggested_skills else "empty",
            (
                "建议：" + "、".join(item.get("name", "") for item in suggested_skills)
                if suggested_skills
                else "无 Skill 建议"
            ),
        )

        await self._emit(on_stage, "ComplianceAgent", "running", "正在做合规检查…")
        compliance = await self.compliance_agent.run(
            answer_result.answer,
            retrieval_result.evidence,
        )
        await self._emit(
            on_stage,
            "ComplianceAgent",
            "success" if compliance.passed else "warning",
            "合规通过" if compliance.passed else "存在合规告警",
        )
        return PipelineResult(
            router_result=router_result,
            retrieval_result=retrieval_result,
            answer_result=answer_result,
            compliance=compliance,
            suggested_skills=suggested_skills,
        )
