"""Database models implemented through the current roadmap phase.

Enterprise ownership rules (see ``specs/001-enterprise-agent-refactor/data-model.md``):

- PostgreSQL is the production business authority.
- Every tenant-owned object carries ``tenant_id``; per-tenant uniqueness is
  expressed as a composite constraint so two tenants can legitimately reuse a
  business code.
- Legacy tables receive ``tenant_id`` as a NULLABLE column during the expand
  stage; the backfill fills it and the enforce migration makes it NOT NULL.
- Timestamps are UTC. Mutable aggregates carry a ``version`` column so updates
  use compare-and-set instead of last-write-wins.

Status and kind columns are free-form ``str`` at the ORM level on purpose: the
allowed value sets live next to the model (see the constants below) and are
enforced as CHECK constraints by the Alembic migrations, which keeps the
constraint visible in the database and avoids native PostgreSQL ENUM types that
are expensive to alter.
"""

from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, TypeDecorator, UniqueConstraint
from sqlmodel import Field, SQLModel

# --- Constrained value sets -------------------------------------------------

TENANT_STATUSES: frozenset[str] = frozenset({"active", "suspended", "deleting"})
USER_STATUSES: frozenset[str] = frozenset({"active", "suspended", "disabled"})
RUN_KINDS: frozenset[str] = frozenset({"chat", "eval", "file_workflow", "reconciliation"})
RUN_STATUSES: frozenset[str] = frozenset(
    {
        "queued",
        "running",
        "waiting_approval",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "recoverable_failed",
        "terminal_failed",
        "timed_out",
    }
)
EVIDENCE_GATES: frozenset[str] = frozenset(
    {"supported", "insufficient_evidence", "not_applicable"}
)
IDEMPOTENCY_STATES: frozenset[str] = frozenset({"in_progress", "completed", "failed"})
AUDIT_OUTCOMES: frozenset[str] = frozenset({"allowed", "denied", "succeeded", "failed"})

#: The tenant that owns every row which predates multi-tenancy. The staged
#: migrations seed exactly this id/code, and ``seed_initial_data`` must join the
#: same tenant rather than invent a second root: a database whose reference data
#: sits in a different tenant than the migrated rows cannot serve either one.
LEGACY_TENANT_ID: Final = "00000000-0000-0000-0000-000000000001"
LEGACY_TENANT_CODE: Final = "legacy"
LEGACY_TENANT_NAME: Final = "Legacy Tenant"

#: Tenant created on a fresh (never-migrated) database so that seeded reference
#: data has an owner from the very first start.
DEFAULT_TENANT_CODE: Final = "default"
DEFAULT_TENANT_NAME: Final = "Default Tenant"


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


class Tenant(SQLModel, table=True):
    """Enterprise tenant; the root of every ownership predicate.

    ``code`` is globally unique because operators address tenants by code, while
    every other business code is unique per tenant.
    """

    __tablename__ = "tenants"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    code: str = Field(unique=True, index=True, max_length=50)
    name: str = Field(max_length=100)
    status: str = Field(default="active", index=True, max_length=20)
    resource_policy_id: str | None = Field(default=None, max_length=36)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Role(SQLModel, table=True):
    """Tenant-owned role carrying an explicit, allow-only action allowlist.

    ``code`` is unique per tenant rather than globally so two tenants can both
    define an ``admin`` role without sharing its meaning.
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
    code: str = Field(index=True, max_length=50)
    name: str = Field(max_length=100)
    description: str = ""
    # Versioned set of permission actions/resources; allow-only, no wildcards.
    actions: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Department(SQLModel, table=True):
    __tablename__ = "departments"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    name: str = Field(max_length=100)
    code: str = Field(unique=True, index=True, max_length=50)
    parent_id: str | None = Field(default=None, foreign_key="departments.id", max_length=36)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class User(SQLModel, table=True):
    """Tenant member.

    ``tenant_id`` and ``external_subject`` are nullable during the expand stage
    so pre-tenant rows remain readable; the enforce migration makes ownership
    mandatory. ``username``/``email`` are still declared globally unique because
    this model must describe the *expand* schema that the migration upgrades
    from; the enforce migration converts both to per-tenant uniqueness, since a
    second tenant must be able to onboard the same natural person.

    ``version`` backs compare-and-set, so a status change made from a stale
    snapshot is refused instead of silently overwriting a concurrent one.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "external_subject"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
    external_subject: str | None = Field(default=None, index=True, max_length=255)
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
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class UserRoleGrant(SQLModel, table=True):
    """Time-bounded grant of a role to a user, with explicit revocation.

    Authorization resolves CURRENT grants, so revocation takes effect on the
    next authorization check rather than at token expiry.
    """

    __tablename__ = "user_role_grants"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "role_id", "scope"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    role_id: str = Field(foreign_key="roles.id", index=True, max_length=36)
    scope: str = Field(default="tenant", max_length=100)
    valid_from: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    granted_by: str | None = Field(default=None, max_length=36)
    revoked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    revoked_by: str | None = Field(default=None, max_length=36)
    revocation_reason: str | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class UserRole(SQLModel, table=True):
    __tablename__ = "user_roles"

    user_id: str = Field(foreign_key="users.id", primary_key=True, max_length=36)
    role_id: str = Field(foreign_key="roles.id", primary_key=True, max_length=36)


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_bases"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
    conversation_id: str = Field(foreign_key="conversations.id", index=True, max_length=36)
    role: str = Field(max_length=20)
    content: str
    meta_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class AIQueryLog(SQLModel, table=True):
    __tablename__ = "ai_query_logs"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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
    tenant_id: str | None = Field(
        default=None, foreign_key="tenants.id", index=True, max_length=36
    )
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


# --- Runs, graph state and evidence correlation -----------------------------


class AgentRun(SQLModel, table=True):
    """One business execution of the shared graph.

    ``run_id`` is the public correlation identifier propagated through API,
    graph, jobs, retrieval, approvals and audit; it is deliberately NOT the
    primary key so internal identity never leaks into correlation surfaces.

    ``thread_id`` is opaque and unique per tenant: a caller must resolve a
    ``GraphCheckpointBinding`` before it may address a LangGraph thread.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "thread_id"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    run_id: str = Field(default_factory=new_id, unique=True, index=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    conversation_id: str | None = Field(
        default=None, foreign_key="conversations.id", index=True, max_length=36
    )
    kind: str = Field(index=True, max_length=30)
    thread_id: str = Field(index=True, max_length=64)
    status: str = Field(default="queued", index=True, max_length=30)
    input_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    result_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    evidence_gate: str = Field(default="not_applicable", max_length=30)
    deadline_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    tool_call_count: int = Field(default=0, ge=0)
    graph_version: str = Field(default="1", max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    started_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class RunEvent(SQLModel, table=True):
    """Append-only durable run milestone.

    ``(run_id, sequence)`` is unique so a client can resume a stream in order;
    ``event_id`` is globally unique and is what the SSE ``id:`` field carries.
    Durable milestones live here; short-lived token fanout may live in Redis.
    """

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    event_id: str = Field(default_factory=new_id, unique=True, index=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str = Field(foreign_key="agent_runs.run_id", index=True, max_length=36)
    sequence: int = Field(ge=1)
    event_type: str = Field(index=True, max_length=60)
    stage: str = Field(default="", max_length=60)
    public_status: str = Field(default="", max_length=60)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    occurred_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    trace_id: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, max_length=128)


class GraphCheckpointBinding(SQLModel, table=True):
    """Authorization gate between a caller and an opaque LangGraph thread.

    Checkpoint payloads stay in the LangGraph saver schema; this table only
    records who may address the thread, under which schema versions.
    """

    __tablename__ = "graph_checkpoint_bindings"
    __table_args__ = (UniqueConstraint("tenant_id", "thread_id"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    run_id: str = Field(foreign_key="agent_runs.run_id", index=True, max_length=36)
    thread_id: str = Field(index=True, max_length=64)
    graph_schema_version: str = Field(default="1", max_length=20)
    checkpoint_schema_version: str = Field(default="1", max_length=20)
    latest_checkpoint_id: str | None = Field(default=None, max_length=64)
    authorization_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class IdempotencyRecord(SQLModel, table=True):
    """Client/connector idempotency scope.

    Reusing a key with a different request digest is a conflict; an in-progress
    duplicate joins the original operation instead of starting a second one.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("tenant_id", "operation", "idempotency_key"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    operation: str = Field(index=True, max_length=100)
    idempotency_key: str = Field(index=True, max_length=128)
    request_digest: str = Field(max_length=64)
    state: str = Field(default="in_progress", index=True, max_length=20)
    response_status: int | None = Field(default=None)
    response_body: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class AuditEvent(SQLModel, table=True):
    """Append-only audit record, idempotent by ``event_id``.

    There is intentionally no ``updated_at``: audit rows are never edited in
    place. Credentials, host paths, raw provider payloads and unrestricted file
    bodies must never reach ``redacted_metadata``.
    """

    __tablename__ = "audit_events"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    event_id: str = Field(default_factory=new_id, unique=True, index=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str | None = Field(default=None, index=True, max_length=36)
    request_id: str | None = Field(default=None, index=True, max_length=128)
    trace_id: str | None = Field(default=None, index=True, max_length=64)
    actor_ref: str = Field(max_length=36)
    authorization_version: int = Field(default=1, ge=1)
    action: str = Field(index=True, max_length=100)
    resource_kind: str = Field(default="", max_length=60)
    resource_id: str | None = Field(default=None, max_length=64)
    outcome: str = Field(index=True, max_length=20)
    reason_code: str | None = Field(default=None, max_length=60)
    redacted_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    occurred_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)

