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


class PlanStep(BaseModel):
    """One normalized plan step for multi-step turns (not an open planner agent)."""

    id: str
    title: str
    kind: Literal["retrieve", "skill", "answer", "tool", "verify"] = "retrieve"
    query: str | None = None
    skill_hint: str | None = None
    tool_hints: list[str] = Field(default_factory=list)
    # Empty = may run in parallel with other ready steps; filled = hard deps.
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "success", "skipped", "error"] = "pending"


class RouterResult(BaseModel):
    domain: str
    task_type: str = "knowledge_qa"
    risk_level: str = "low"
    need_skill: bool = False
    tool_hints: list[str] = Field(default_factory=list)
    rewrite_query: str | None = None
    # Structured plan fields — Router routing, not open-ended Planner Agent.
    complexity: Literal["simple", "multi_step"] = "simple"
    plan_steps: list[PlanStep] = Field(default_factory=list)
    plan_source: Literal["none", "user", "router"] = "none"


class ComplianceResult(BaseModel):
    passed: bool
    warnings: list[str] = Field(default_factory=list)


class UsedMemoryItem(BaseModel):
    """One memory fragment used (or considered) for this turn."""

    id: str | None = None
    memory_type: str = "unknown"
    content: str
    source_slot: Literal["fixed", "recalled", "rolling_summary", "history"] = "recalled"
    confidence: float | None = None
    rank_score: float | None = None


class ToolCallTrace(BaseModel):
    """Tool invocation recorded for this turn."""

    tool_name: str
    status: str = "success"
    agent_name: str | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    latency_ms: int = 0


class CommandTrace(BaseModel):
    """Pipeline / agent step executed for this turn."""

    name: str
    status: str = "success"
    summary: str = ""
    output: dict[str, Any] = Field(default_factory=dict)


class TurnDiagnostics(BaseModel):
    """Request-scoped diagnostics shown in the chat UI."""

    memories: list[UsedMemoryItem] = Field(default_factory=list)
    tools: list[ToolCallTrace] = Field(default_factory=list)
    commands: list[CommandTrace] = Field(default_factory=list)


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
    diagnostics: TurnDiagnostics = Field(default_factory=TurnDiagnostics)


class AssistantMessageMetadata(BaseModel):
    citations: list[Citation] = Field(default_factory=list)
    query_log_id: str | None = None
    confidence_score: float | None = None
    query_mode: str | None = None
    router_result: RouterResult | None = None
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    compliance: ComplianceResult | None = None
    diagnostics: TurnDiagnostics = Field(default_factory=TurnDiagnostics)


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
