"""Cross-retriever hybrid fusion of LightRAG and BM25 via RRF."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from backend.app.core.exceptions import ApplicationError
from backend.app.rag.protocols import Retriever
from backend.app.schemas.retrieval import Evidence, RetrievalRequest

DEFAULT_RRF_K = 60


class HybridRetriever:
    name = "hybrid_lightrag_bm25"

    def __init__(
        self,
        lightrag: Retriever | None = None,
        bm25: Retriever | None = None,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self.lightrag = lightrag
        self.bm25 = bm25
        self.rrf_k = rrf_k

    @property
    def available(self) -> bool:
        return (
            self.lightrag is not None
            and self.bm25 is not None
            and self.lightrag.available
            and self.bm25.available
        )

    @staticmethod
    def _document_key(evidence: Evidence) -> str | None:
        if evidence.document_id:
            return f"document:{evidence.document_id}"
        return None

    @staticmethod
    def _chunk_key(evidence: Evidence) -> str:
        if evidence.document_id and evidence.chunk_id:
            return f"document:{evidence.document_id}:chunk:{evidence.chunk_id}"
        if evidence.document_id:
            return f"document:{evidence.document_id}"
        if evidence.source_id:
            return f"source:{evidence.source_id}"
        return f"snippet:{hash(evidence.snippet.strip().lower())}"

    def _rrf_contribution(self, rank: int) -> float:
        return 1.0 / (self.rrf_k + max(rank, 1))

    def _fuse(
        self,
        lightrag_hits: Sequence[Evidence],
        bm25_hits: Sequence[Evidence],
        limit: int,
    ) -> list[Evidence]:
        bm25_by_document: dict[str, Evidence] = {}
        for item in bm25_hits:
            key = self._document_key(item)
            if key is not None and key not in bm25_by_document:
                bm25_by_document[key] = item

        fused: dict[str, Evidence] = {}
        rrf_scores: dict[str, float] = {}
        claimed_documents: set[str] = set()

        for item in lightrag_hits:
            key = self._chunk_key(item)
            document_key = self._document_key(item)
            bm25_match = bm25_by_document.get(document_key) if document_key else None
            score = self._rrf_contribution(item.rank)
            source_retrievers = ["lightrag"]
            metadata = {
                "fusion": "rrf",
                "rrf_k": self.rrf_k,
                "lightrag_rank": item.rank,
                "lightrag_score": item.score,
                "bm25_rank": None,
                "bm25_score": None,
                "source_retrievers": source_retrievers,
            }
            if bm25_match is not None:
                score += self._rrf_contribution(bm25_match.rank)
                source_retrievers = ["lightrag", "bm25"]
                metadata["bm25_rank"] = bm25_match.rank
                metadata["bm25_score"] = bm25_match.score
                metadata["source_retrievers"] = source_retrievers
                if document_key is not None:
                    claimed_documents.add(document_key)
            fused[key] = item.model_copy(
                update={
                    "score": score,
                    "retriever_type": self.name,
                    "metadata": {**item.metadata, **metadata},
                }
            )
            rrf_scores[key] = score

        for item in bm25_hits:
            document_key = self._document_key(item)
            if document_key is not None and document_key in claimed_documents:
                continue
            key = self._chunk_key(item)
            if key in fused:
                continue
            score = self._rrf_contribution(item.rank)
            fused[key] = item.model_copy(
                update={
                    "score": score,
                    "retriever_type": self.name,
                    "metadata": {
                        **item.metadata,
                        "fusion": "rrf",
                        "rrf_k": self.rrf_k,
                        "lightrag_rank": None,
                        "lightrag_score": None,
                        "bm25_rank": item.rank,
                        "bm25_score": item.score,
                        "source_retrievers": ["bm25"],
                    },
                }
            )
            rrf_scores[key] = score

        ordered = sorted(
            fused.items(),
            key=lambda pair: (
                -rrf_scores[pair[0]],
                pair[1].document_id or "",
                pair[1].chunk_id or "",
            ),
        )
        return [
            evidence.model_copy(update={"rank": rank, "score": rrf_scores[key]})
            for rank, (key, evidence) in enumerate(ordered[:limit], start=1)
        ]

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        if not self.available:
            raise ApplicationError(
                "RETRIEVAL_STRATEGY_UNAVAILABLE",
                "Hybrid LightRAG/BM25 retrieval is not enabled",
                503,
            )
        assert self.lightrag is not None
        assert self.bm25 is not None
        lightrag_hits, bm25_hits = await asyncio.gather(
            self.lightrag.retrieve(request, limit),
            self.bm25.retrieve(request, limit),
        )
        return self._fuse(lightrag_hits, bm25_hits, limit)
