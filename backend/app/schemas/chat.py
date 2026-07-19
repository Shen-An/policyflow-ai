"""Chat API schemas."""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from backend.app.schemas.retrieval import LightRAGQueryMode, RetrievalStrategy

Difficulty = Literal["simple", "multi_step", "branched"]
ReasoningMode = Literal["cot_direct", "cot_steps", "tot_select"]
TurnStatus = Literal["completed", "awaiting_plan_selection", "cancelled"]


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    # Required for a new question; optional when resuming ToT selection.
    question: str | None = Field(default=None, max_length=4000)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    enable_skill: bool = True
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_LIGHTRAG_BM25
    query_mode: LightRAGQueryMode = LightRAGQueryMode.HYBRID
    top_k: int = Field(default=5, ge=1, le=20)
    rerank_enabled: bool = False
    # ToT HITL: pick a previously emitted plan option (dual-request).
    selected_option_id: str | None = None
    cancel_pending_plan: bool = False

    @model_validator(mode="after")
    def _require_question_or_selection(self) -> Self:
        has_selection = bool(self.selected_option_id) or bool(self.cancel_pending_plan)
        q = (self.question or "").strip()
        if not has_selection and not q:
            raise ValueError("question is required unless selected_option_id or cancel_pending_plan is set")
        if q and len(q) < 1:
            raise ValueError("question must not be empty")
        if self.question is not None:
            self.question = self.question.strip()
        return self


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


class PlanOption(BaseModel):
    """One candidate execution path for ToT-style selection (not academic ToT search)."""

    id: str
    title: str
    summary: str = ""
    tradeoffs: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    recommended: bool = False


class RouterResult(BaseModel):
    domain: str
    task_type: str = "knowledge_qa"
    risk_level: str = "low"
    need_skill: bool = False
    tool_hints: list[str] = Field(default_factory=list)
    rewrite_query: str | None = None
    # Structured plan fields — Router routing, not open-ended Planner Agent.
    # complexity remains simple|multi_step for backward compat (branched maps to multi_step).
    complexity: Literal["simple", "multi_step"] = "simple"
    # difficulty is the honest three-tier signal; reasoning_mode is what we run.
    difficulty: Difficulty = "simple"
    reasoning_mode: ReasoningMode = "cot_direct"
    plan_steps: list[PlanStep] = Field(default_factory=list)
    plan_options: list[PlanOption] = Field(default_factory=list)
    plan_source: Literal["none", "user", "router", "user_selected"] = "none"


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
    # Centralized turn error ledger (from TurnState / PipelineResult.errors).
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    query_log_id: str = ""
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    confidence_score: float = 0.0
    query_mode: str = "hybrid"
    router_result: RouterResult
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    draft: None = None
    compliance: ComplianceResult = Field(default_factory=lambda: ComplianceResult(passed=True, warnings=[]))
    diagnostics: TurnDiagnostics = Field(default_factory=TurnDiagnostics)
    # Dual-request ToT HITL: awaiting_plan_selection ends stream without a full answer.
    status: TurnStatus = "completed"
    reasoning_mode: ReasoningMode = "cot_direct"
    plan_options: list[PlanOption] = Field(default_factory=list)
    awaiting_message_id: str | None = None


class AssistantMessageMetadata(BaseModel):
    citations: list[Citation] = Field(default_factory=list)
    query_log_id: str | None = None
    confidence_score: float | None = None
    query_mode: str | None = None
    router_result: RouterResult | None = None
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    compliance: ComplianceResult | None = None
    diagnostics: TurnDiagnostics = Field(default_factory=TurnDiagnostics)
    # ToT HITL durable state on assistant stub messages.
    turn_status: TurnStatus | None = None
    reasoning_mode: ReasoningMode | None = None
    plan_options: list[PlanOption] = Field(default_factory=list)
    pending_plan: dict[str, Any] = Field(default_factory=dict)
    selected_option_id: str | None = None


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
