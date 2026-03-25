"""Unified retrieval strategy orchestration."""

import hashlib
import re
from time import perf_counter

from backend.app.core.exceptions import ApplicationError
from backend.app.rag.bm25_retriever import BM25Retriever
from backend.app.rag.hybrid_retriever import HybridRetriever
from backend.app.rag.protocols import Reranker, Retriever
from backend.app.rag.rerank_service import RerankService
from backend.app.schemas.retrieval import (
    Evidence,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStrategy,
    RetrievalTraceItem,
)


def _terms(value: str) -> set[str]:
    normalized = value.lower()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {term for term in words if term}


class RAGService:
    def __init__(
        self,
        lightrag: Retriever,
        bm25: Retriever | None = None,
        hybrid: Retriever | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.lightrag = lightrag
        self.bm25 = bm25 or BM25Retriever()
        self.hybrid = hybrid or HybridRetriever()
        self.reranker = reranker or RerankService()

    @staticmethod
    def _identity(evidence: Evidence) -> str:
        if evidence.document_id and evidence.chunk_id:
            return f"document:{evidence.document_id}:chunk:{evidence.chunk_id}"
        if evidence.document_id:
            snippet_hash = hashlib.sha256(evidence.snippet.strip().lower().encode()).hexdigest()
            return f"document:{evidence.document_id}:snippet:{snippet_hash}"
        if evidence.source_id:
            return f"source:{evidence.source_id}"
        return hashlib.sha256(evidence.snippet.strip().lower().encode()).hexdigest()

    def _retriever(self, strategy: RetrievalStrategy) -> Retriever:
        if strategy == RetrievalStrategy.LIGHTRAG_ONLY:
            return self.lightrag
        if strategy == RetrievalStrategy.BM25_ONLY:
            return self.bm25
        if strategy == RetrievalStrategy.HYBRID_LIGHTRAG_BM25:
            return self.hybrid
        raise ApplicationError("RETRIEVAL_CONFIGURATION_INVALID", "Unknown retrieval strategy", 422)

    def validate_configuration(
        self,
        strategy: RetrievalStrategy,
        rerank_enabled: bool,
    ) -> None:
        retriever = self._retriever(strategy)
        if not retriever.available:
            raise ApplicationError(
                "RETRIEVAL_STRATEGY_UNAVAILABLE",
                "Requested retrieval strategy is unavailable",
                503,
                {"strategy": strategy.value},
            )
        if rerank_enabled and not self.reranker.available:
            raise ApplicationError("RERANKER_UNAVAILABLE", "Reranker is not configured", 503)

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        started_at = perf_counter()
        self.validate_configuration(request.strategy, request.rerank_enabled)
        retriever = self._retriever(request.strategy)
        candidates = await retriever.retrieve(request, request.resolved_candidate_k)
        query_terms = _terms(request.query)

        def cross_knowledge_base_score(evidence: Evidence) -> float:
            evidence_terms = _terms(
                f"{evidence.knowledge_base_name} {evidence.document_title or ''} {evidence.snippet}"
            )
            lexical_score = (
                len(query_terms & evidence_terms) / len(query_terms) if query_terms else 0.0
            )
            return lexical_score * 0.7 + (evidence.score or 0.0) * 0.3

        if not request.rerank_enabled:
            candidates.sort(
                key=lambda evidence: (
                    -cross_knowledge_base_score(evidence),
                    evidence.knowledge_base_id,
                    evidence.rank,
                )
            )
        deduplicated: list[Evidence] = []
        seen: set[str] = set()
        for candidate in candidates:
            identity = self._identity(candidate)
            if identity not in seen:
                seen.add(identity)
                deduplicated.append(candidate)

        rerank_applied = False
        if request.rerank_enabled:
            deduplicated = await self.reranker.rerank(
                request.query,
                deduplicated,
                request.top_k,
            )
            rerank_applied = True

        ranked = [
            evidence.model_copy(update={"rank": rank})
            for rank, evidence in enumerate(deduplicated[: request.top_k], start=1)
        ]
        trace = [
            RetrievalTraceItem(
                **evidence.model_dump(),
                retrieval_strategy=request.strategy,
                lightrag_query_mode=request.lightrag_query_mode,
            )
            for evidence in ranked
        ]
        latency_ms = int((perf_counter() - started_at) * 1000)
        return RetrievalResult(
            evidence=ranked,
            trace=trace,
            latency_ms=latency_ms,
            rerank_applied=rerank_applied,
        )
