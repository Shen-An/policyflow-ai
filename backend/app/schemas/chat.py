"""Chat API schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.schemas.retrieval import LightRAGQueryMode, RetrievalStrategy


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    question: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    enable_skill: bool = True
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_LIGHTRAG_BM25
    query_mode: LightRAGQueryMode = LightRAGQueryMode.HYBRID
    top_k: int = Field(default=5, ge=1, le=20)
    rerank_enabled: bool = False


class Citation(BaseModel):
    knowledge_base_id: str
    knowledge_base_name: str
    document_id: str | None
    document_title: str | None
    chunk_id: str | None
    snippet: str
    score: float | None


class RouterResult(BaseModel):
    domain: str
    task_type: str = "knowledge_qa"
    risk_level: str = "low"


class ComplianceResult(BaseModel):
    passed: bool
    warnings: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    query_log_id: str
    answer: str
    citations: list[Citation]
    confidence_score: float
    query_mode: str
    router_result: RouterResult
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    draft: None = None
    compliance: ComplianceResult


class AssistantMessageMetadata(BaseModel):
    citations: list[Citation] = Field(default_factory=list)
    query_log_id: str | None = None
    confidence_score: float | None = None
    query_mode: str | None = None
    router_result: RouterResult | None = None
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    compliance: ComplianceResult | None = None


class MessageRead(BaseModel):
    id: str
    role: str
    content: str
    meta_json: AssistantMessageMetadata
    created_at: datetime


class ConversationRead(BaseModel):
    id: str
    title: str
    status: str
    summary: dict[str, Any]
    messages: list[MessageRead]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    status: str
    message_count: int
    last_message_preview: str | None = None
    last_message_role: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int
    page: int
    page_size: int


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


FeedbackRating = Literal["useful", "not_useful", "wrong_citation", "incomplete"]


class QueryFeedbackCreate(BaseModel):
    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=1000)


class QueryFeedbackRead(BaseModel):
    id: str
    query_log_id: str
    user_id: str
    rating: FeedbackRating
    comment: str | None
    created_at: datetime
    updated_at: datetime
