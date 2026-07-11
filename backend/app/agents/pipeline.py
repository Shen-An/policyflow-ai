"""Router, retrieval, answer, Skill, and compliance orchestration."""

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.base import PipelineResult
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.db.models import KnowledgeBase
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalResult


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

    async def run(
        self,
        question: str,
        knowledge_bases: list[KnowledgeBase],
        retrieval_request: RetrievalRequest | None,
        enable_skill: bool = True,
    ) -> PipelineResult:
        router_result = await self.router_agent.run(question, knowledge_bases)
        retrieval_result = (
            await self.retrieval_agent.run(retrieval_request)
            if retrieval_request is not None
            else RetrievalResult(evidence=[], trace=[], latency_ms=0)
        )
        answer_result = await self.answer_agent.run(question, retrieval_result.evidence)
        suggested_skills = await self.skill_agent.run(question, router_result, enable_skill)
        compliance = await self.compliance_agent.run(
            answer_result.answer,
            retrieval_result.evidence,
        )
        return PipelineResult(
            router_result=router_result,
            retrieval_result=retrieval_result,
            answer_result=answer_result,
            compliance=compliance,
            suggested_skills=suggested_skills,
        )
