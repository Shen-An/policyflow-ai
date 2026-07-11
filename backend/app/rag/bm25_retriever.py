"""Explicit BM25 placeholder for the MVP."""

from backend.app.core.exceptions import ApplicationError
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class BM25Retriever:
    name = "bm25"

    @property
    def available(self) -> bool:
        return False

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        raise ApplicationError(
            "RETRIEVAL_STRATEGY_UNAVAILABLE",
            "BM25 retriever is not enabled",
            503,
        )
