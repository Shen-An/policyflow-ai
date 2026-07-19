"""Database models implemented through the current roadmap phase."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, TypeDecorator, UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Store UTC as naive datetime; rehydrate as timezone-aware UTC.

    SQLite drops tzinfo on round-trip. Without this, API JSON omits `Z` and
    browsers treat the timestamp as local time (e.g. UTC 08:54 → 08:54 local).
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    code: str = Field(unique=True, index=True, max_length=50)
    name: str = Field(max_length=100)
    description: str = ""
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Department(SQLModel, table=True):
    __tablename__ = "departments"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(max_length=100)
    code: str = Field(unique=True, index=True, max_length=50)
    parent_id: str | None = Field(default=None, foreign_key="departments.id", max_length=36)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    username: str = Field(unique=True, index=True, max_length=64)
    email: str = Field(unique=True, index=True, max_length=255)
    password_hash: str = Field(max_length=255)
    display_name: str = Field(max_length=100)
    department_id: str | None = Field(
        default=None,
        foreign_key="departments.id",
        index=True,
        max_length=36,
    )
    status: str = Field(default="active", index=True, max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class UserRole(SQLModel, table=True):
    __tablename__ = "user_roles"

    user_id: str = Field(foreign_key="users.id", primary_key=True, max_length=36)
    role_id: str = Field(foreign_key="roles.id", primary_key=True, max_length=36)


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_bases"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(max_length=100)
    code: str = Field(unique=True, index=True, max_length=50)
    department_id: str = Field(foreign_key="departments.id", index=True, max_length=36)
    description: str = ""
    rag_workspace: str = Field(max_length=255)
    default_query_mode: str = Field(default="mix", max_length=20)
    status: str = Field(default="active", index=True, max_length=20)
    created_by: str = Field(default="system", max_length=36)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class KnowledgeBasePermission(SQLModel, table=True):
    __tablename__ = "knowledge_base_permissions"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    knowledge_base_id: str = Field(
        foreign_key="knowledge_bases.id",
        index=True,
        max_length=36,
    )
    subject_type: str = Field(index=True, max_length=20)
    subject_id: str = Field(index=True, max_length=36)
    permission: str = Field(max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class KnowledgeDocument(SQLModel, table=True):
    __tablename__ = "knowledge_documents"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    knowledge_base_id: str = Field(
        foreign_key="knowledge_bases.id",
        index=True,
        max_length=36,
    )
    title: str = Field(max_length=255)
    file_path: str = Field(max_length=500)
    file_type: str = Field(max_length=20)
    content_text: str | None = None
    content_hash: str = Field(index=True, max_length=128)
    external_id: str | None = Field(default=None, index=True, max_length=128)
    index_status: str = Field(default="pending", index=True, max_length=20)
    index_error: str | None = None
    source_version: int = 1
    created_by: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class RagIndexJob(SQLModel, table=True):
    __tablename__ = "rag_index_jobs"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    knowledge_document_id: str = Field(
        foreign_key="knowledge_documents.id",
        index=True,
        max_length=36,
    )
    job_type: str = Field(default="insert", max_length=20)
    status: str = Field(default="pending", index=True, max_length=20)
    started_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    actor_id: str | None = Field(default=None, index=True, max_length=36)
    action: str = Field(index=True, max_length=100)
    target_type: str = Field(max_length=100)
    target_id: str | None = Field(default=None, index=True, max_length=36)
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    ip_address: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, index=True, max_length=128)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class ModelProvider(SQLModel, table=True):
    __tablename__ = "model_providers"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(unique=True, index=True, max_length=100)
    provider_type: str = Field(default="openai_compatible", max_length=50)
    capability: str = Field(default="chat", index=True, max_length=20)
    base_url: str | None = Field(default=None, max_length=500)
    api_key_env: str = Field(max_length=100)
    api_key_ciphertext: str | None = None
    default_chat_model: str = Field(max_length=100)
    default_embedding_model: str | None = Field(default=None, max_length=100)
    enabled: bool = True
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    title: str = Field(max_length=255)
    channel: str = Field(default="api", max_length=20)
    status: str = Field(default="active", index=True, max_length=20)
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    conversation_id: str = Field(foreign_key="conversations.id", index=True, max_length=36)
    role: str = Field(max_length=20)
    content: str
    meta_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class AIQueryLog(SQLModel, table=True):
    __tablename__ = "ai_query_logs"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    conversation_id: str = Field(foreign_key="conversations.id", index=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    question: str
    answer: str
    knowledge_base_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    retrieved_sources: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    confidence_score: float = 0.0
    query_mode: str = Field(default="hybrid", max_length=20)
    latency_ms: int = 0
    token_usage: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class QueryFeedback(SQLModel, table=True):
    __tablename__ = "query_feedback"
    __table_args__ = (UniqueConstraint("query_log_id", "user_id"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    query_log_id: str = Field(foreign_key="ai_query_logs.id", index=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    rating: str = Field(max_length=30)
    comment: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Skill(SQLModel, table=True):
    __tablename__ = "skills"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(unique=True, index=True, max_length=100)
    version: str = Field(default="1.0.0", max_length=50)
    description: str
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    risk_level: str = Field(default="low", max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Tool(SQLModel, table=True):
    __tablename__ = "tools"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(unique=True, index=True, max_length=100)
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    output_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    risk_level: str = Field(default="low", max_length=20)
    enabled: bool = True
    timeout_seconds: int = 30
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class ToolCallLog(SQLModel, table=True):
    __tablename__ = "tool_call_logs"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    conversation_id: str | None = Field(default=None, index=True, max_length=36)
    agent_name: str = Field(max_length=100)
    tool_name: str = Field(index=True, max_length=100)
    user_id: str | None = Field(default=None, index=True, max_length=36)
    request_id: str | None = Field(default=None, index=True, max_length=128)
    input_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    output_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(index=True, max_length=20)
    error_message: str | None = None
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Draft(SQLModel, table=True):
    __tablename__ = "drafts"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    conversation_id: str | None = Field(
        default=None,
        foreign_key="conversations.id",
        index=True,
        max_length=36,
    )
    draft_type: str = Field(index=True, max_length=30)
    title: str = Field(max_length=255)
    content: str
    source_question: str
    related_sources: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    status: str = Field(default="draft", index=True, max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class MCPServer(SQLModel, table=True):
    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(unique=True, index=True, max_length=100)
    server_type: str = Field(default="mock", index=True, max_length=20)
    integration_mode: str = Field(default="mock", index=True, max_length=20)
    endpoint: str | None = Field(default=None, max_length=500)
    command: str = Field(default="", max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    enabled: bool = False
    health_status: str = Field(default="unknown", index=True, max_length=20)
    tools: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    last_error_code: str | None = Field(default=None, max_length=100)
    last_error_message: str | None = None
    last_checked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class MemoryItem(SQLModel, table=True):
    __tablename__ = "memory_items"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_type: str = Field(index=True, max_length=20)
    owner_id: str = Field(index=True, max_length=36)
    memory_type: str = Field(index=True, max_length=50)
    content: str
    source: str = Field(max_length=20)
    confidence: float = 0.5
    embedding: list[float] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    meta_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class FAQDraft(SQLModel, table=True):
    __tablename__ = "faq_drafts"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    knowledge_base_id: str = Field(foreign_key="knowledge_bases.id", index=True, max_length=36)
    source_document_id: str | None = Field(default=None, index=True, max_length=36)
    source_conversation_id: str | None = Field(default=None, index=True, max_length=36)
    question: str
    answer: str
    status: str = Field(default="draft", index=True, max_length=30)
    generated_by: str = Field(default="ai", max_length=20)
    reviewer_id: str | None = Field(default=None, max_length=36)
    review_note: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class EvalCase(SQLModel, table=True):
    __tablename__ = "eval_cases"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    question: str
    category: str = Field(index=True, max_length=50)
    expected_answer_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    expected_source_documents: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    expected_chunk_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    should_answer: bool = True
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class RetrievalEvalItem(SQLModel, table=True):
    __tablename__ = "retrieval_eval_items"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    eval_case_id: str | None = Field(default=None, index=True, max_length=36)
    query: str
    knowledge_base_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    relevant_document_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    relevant_chunk_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    relevance_judgement: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(max_length=255)
    status: str = Field(default="pending", index=True, max_length=20)
    total_cases: int = 0
    started_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    config_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_by: str | None = Field(default=None, max_length=36)
    error_summary: str | None = None
    request_id: str | None = Field(default=None, index=True, max_length=128)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class EvalResult(SQLModel, table=True):
    __tablename__ = "eval_results"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    eval_run_id: str = Field(foreign_key="eval_runs.id", index=True, max_length=36)
    eval_case_id: str | None = Field(default=None, index=True, max_length=36)
    retrieval_eval_item_id: str | None = Field(default=None, index=True, max_length=36)
    question: str
    answer: str | None = None
    retrieved_sources: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    retrieval_metrics: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    answer_metrics: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    ragas_metrics: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    type_statuses: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    score: float = 0.0
    passed: bool = False
    error_message: str | None = None
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
