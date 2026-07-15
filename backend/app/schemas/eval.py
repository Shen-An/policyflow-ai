"""Evaluation API schemas."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.schemas.retrieval import LightRAGQueryMode, RetrievalStrategy, RetrievalTraceItem


class EvalCaseCreate(BaseModel):
    question: str = Field(min_length=1)
    category: str
    expected_answer_keywords: list[str] = Field(default_factory=list)
    expected_source_documents: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    should_answer: bool = True


class EvalCaseRead(EvalCaseCreate):
    id: str
    enabled: bool
    created_at: datetime


class RetrievalEvalItemCreate(BaseModel):
    eval_case_id: str | None = None
    query: str = Field(min_length=1)
    knowledge_base_ids: list[str] = Field(min_length=1)
    relevant_document_ids: list[str] = Field(default_factory=list)
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    relevance_judgement: dict[str, Any] | None = None


class RetrievalEvalItemRead(RetrievalEvalItemCreate):
    id: str
    enabled: bool
    created_at: datetime


class RetrievalConfig(BaseModel):
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_LIGHTRAG_BM25
    top_k_values: list[Annotated[int, Field(ge=1, le=100)]] = Field(
        default_factory=lambda: [1, 3, 5, 10],
        min_length=1,
    )
    rerank_enabled: bool = False
    query_mode: LightRAGQueryMode = LightRAGQueryMode.HYBRID

    @field_validator("top_k_values")
    @classmethod
    def normalize_top_k_values(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class RagasConfig(BaseModel):
    enabled: bool = False
    metrics: list[str] = Field(default_factory=list)


EvalType = Literal["retrieval", "rag_answer", "ragas"]


def default_eval_types() -> list[EvalType]:
    return ["retrieval"]


class EvalRunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    case_ids: list[str] = Field(default_factory=list)
    retrieval_item_ids: list[str] = Field(default_factory=list)
    eval_types: list[EvalType] = Field(default_factory=default_eval_types, min_length=1)
    retrieval_config: RetrievalConfig = Field(default_factory=RetrievalConfig)
    # Optional multi-strategy comparison: runs retrieval once per strategy and
    # stores per-strategy aggregates under metrics["strategy_comparison"].
    compare_strategies: list[RetrievalStrategy] = Field(default_factory=list)
    ragas_config: RagasConfig = Field(default_factory=RagasConfig)

    @field_validator("case_ids", "retrieval_item_ids", "eval_types", "compare_strategies")
    @classmethod
    def deduplicate_values(cls, value: list[Any]) -> list[Any]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_selection(self) -> "EvalRunCreate":
        if not self.case_ids and not self.retrieval_item_ids:
            raise ValueError("At least one case or retrieval item is required")
        if "retrieval" in self.eval_types and not self.retrieval_item_ids:
            raise ValueError("retrieval eval requires retrieval_item_ids")
        if {"rag_answer", "ragas"} & set(self.eval_types) and not self.case_ids:
            raise ValueError("rag_answer and ragas eval require case_ids")
        if self.compare_strategies and "retrieval" not in self.eval_types:
            raise ValueError("compare_strategies requires retrieval eval_types")
        return self


class EvalResultRead(BaseModel):
    id: str
    question: str
    answer: str | None
    retrieved_sources: list[dict[str, Any]]
    retrieval_metrics: dict[str, Any] | None
    answer_metrics: dict[str, Any] | None
    ragas_metrics: dict[str, Any] | None
    type_statuses: dict[str, str]
    score: float
    passed: bool
    error_message: str | None
    latency_ms: int


class EvalRunRead(BaseModel):
    id: str
    name: str
    status: str
    total_cases: int
    metrics: dict[str, Any]
    config_snapshot: dict[str, Any]
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_summary: str | None
    request_id: str | None
    results: list[EvalResultRead]


class EvalRunSummary(BaseModel):
    id: str
    name: str
    status: str
    total_cases: int
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    metrics: dict[str, Any]
    error_summary: str | None
    request_id: str | None


class EvalRunListResponse(BaseModel):
    items: list[EvalRunSummary]
    total: int
    page: int
    page_size: int


class RetrievalDebugRequest(BaseModel):
    query: str = Field(min_length=1)
    knowledge_base_ids: list[str] = Field(min_length=1)
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_LIGHTRAG_BM25
    top_k: int = Field(default=10, ge=1, le=100)
    rerank_enabled: bool = False
    query_mode: LightRAGQueryMode = LightRAGQueryMode.HYBRID


class RetrievalDebugResponse(BaseModel):
    query: str
    strategy: RetrievalStrategy
    lightrag_query_mode: LightRAGQueryMode
    rerank_applied: bool
    items: list[RetrievalTraceItem]
    warnings: list[str]
