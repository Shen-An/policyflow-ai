"""Explicit cross-retriever hybrid placeholder for the MVP."""

from backend.app.core.exceptions import ApplicationError
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class HybridRetriever:
    name = "hybrid_lightrag_bm25"

    @property
    def available(self) -> bool:
        return False

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        raise ApplicationError(
            "RETRIEVAL_STRATEGY_UNAVAILABLE",
            "Hybrid LightRAG/BM25 retrieval is not enabled",
            503,
        )
