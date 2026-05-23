"""Unified retrieval schemas and strategy enums."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RetrievalStrategy(StrEnum):
    LIGHTRAG_ONLY = "lightrag_only"
    BM25_ONLY = "bm25_only"
    HYBRID_LIGHTRAG_BM25 = "hybrid_lightrag_bm25"


class LightRAGQueryMode(StrEnum):
    NAIVE = "naive"
    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"
    MIX = "mix"


class Evidence(BaseModel):
    knowledge_base_id: str
    knowledge_base_name: str
    document_id: str | None = None
    document_title: str | None = None
    document_version: int | None = None
    chunk_id: str | None = None
    source_id: str | None = None
    snippet: str
    score: float | None = None
    rerank_score: float | None = None
    retriever_type: str
    rank: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(min_length=1)
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_LIGHTRAG_BM25
    top_k: int = Field(default=5, ge=1, le=100)
    candidate_k: int | None = Field(default=None, ge=1, le=400)
    rerank_enabled: bool = False
    lightrag_query_mode: LightRAGQueryMode = LightRAGQueryMode.HYBRID

    @property
    def resolved_candidate_k(self) -> int:
        if self.candidate_k is not None:
            return self.candidate_k
        if self.rerank_enabled:
            return max(self.top_k * 4, 20)
        return self.top_k


class RetrievalTraceItem(Evidence):
    retrieval_strategy: RetrievalStrategy
    lightrag_query_mode: LightRAGQueryMode


class RetrievalResult(BaseModel):
    evidence: list[Evidence]
    trace: list[RetrievalTraceItem]
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int
    rerank_applied: bool = False
