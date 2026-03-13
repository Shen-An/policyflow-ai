"""Retrieval Agent that only consumes the RAGService contract."""

from backend.app.schemas.retrieval import RetrievalRequest, RetrievalResult
from backend.app.services.rag_service import RAGService


class RetrievalAgent:
    def __init__(self, rag_service: RAGService) -> None:
        self.rag_service = rag_service

    async def run(self, request: RetrievalRequest) -> RetrievalResult:
        return await self.rag_service.retrieve(request)
