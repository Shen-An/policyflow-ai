"""Retrieval Agent that only consumes the RAGService contract."""

from backend.app.agents.turn_budget import current_turn_budget, reserve_current
from backend.app.core.exceptions import ApplicationError
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalResult
from backend.app.services.rag_service import RAGService


class RetrievalAgent:
    def __init__(self, rag_service: RAGService) -> None:
        self.rag_service = rag_service

    async def run(self, request: RetrievalRequest) -> RetrievalResult:
        reserve_current("retrieval")
        budget = current_turn_budget.get()
        try:
            if budget is not None:
                return await budget.wait_for(self.rag_service.retrieve(request))
            return await self.rag_service.retrieve(request)
        except TimeoutError as exc:
            raise ApplicationError("RETRIEVAL_TIMEOUT", "检索超时", 504) from exc
