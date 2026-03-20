"""Reranker placeholder with explicit unavailable behavior."""

from collections.abc import Sequence

from backend.app.core.exceptions import ApplicationError
from backend.app.schemas.retrieval import Evidence


class RerankService:
    @property
    def available(self) -> bool:
        return False

    async def rerank(
        self,
        query: str,
        candidates: Sequence[Evidence],
        limit: int,
    ) -> list[Evidence]:
        raise ApplicationError("RERANKER_UNAVAILABLE", "Reranker is not configured", 503)
