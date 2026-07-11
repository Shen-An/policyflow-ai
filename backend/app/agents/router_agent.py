"""Deterministic MVP question router."""

from backend.app.db.models import KnowledgeBase
from backend.app.schemas.chat import RouterResult


class RouterAgent:
    async def run(self, question: str, knowledge_bases: list[KnowledgeBase]) -> RouterResult:
        lowered = question.lower()
        domain = knowledge_bases[0].code if len(knowledge_bases) == 1 else "general"
        risk_level = "high" if any(term in lowered for term in ("法律", "合规", "处罚")) else "low"
        return RouterResult(domain=domain, risk_level=risk_level)
