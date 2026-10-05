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

from collections.abc import Iterable
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

#: DurableJob lifecycle. ``queued`` rows are eligible for lease; ``leased`` and
#: ``running`` are owned by a worker with a live lease; the three ``*_failed`` /
#: ``cancelled`` / ``succeeded`` states are terminal. ``recoverable_failed`` is a
#: transient parking state the service re-queues under a bounded attempt budget.
JOB_STATES: frozenset[str] = frozenset(
    {
        "queued",
        "leased",
        "running",
        "cancel_requested",
        "succeeded",
        "recoverable_failed",
        "terminal_failed",
        "cancelled",
    }
)
JOB_TERMINAL_STATES: frozenset[str] = frozenset(
    {"succeeded", "terminal_failed", "cancelled"}
)
#: Transactional-outbox delivery lifecycle. A row is created ``pending`` inside
#: the business transaction, claimed to ``publishing`` by the publisher, and then
#: ``delivered`` after a broker publisher-confirm or ``dead_letter`` after the
#: bounded retry budget is exhausted.
OUTBOX_DELIVERY_STATES: frozenset[str] = frozenset(
    {"pending", "publishing", "delivered", "dead_letter"}
)
#: Quota admission scopes. A policy applies to exactly one scope; global rows
#: carry a null ``tenant_id``.
QUOTA_SCOPES: frozenset[str] = frozenset({"global", "tenant", "user"})
#: QuotaLease outcome once released or reaped.
QUOTA_LEASE_OUTCOMES: frozenset[str] = frozenset({"held", "released", "expired"})
#: CapacityTestRun LLM mode; a mock run and a real-provider run measure different
#: systems and their numbers must never be merged into one claim.
CAPACITY_LLM_MODES: frozenset[str] = frozenset({"deterministic_mock", "real_provider"})
CAPACITY_VERDICTS: frozenset[str] = frozenset({"pass", "fail", "inconclusive"})

# --- Stage 5: materials, object versions and vector manifests ---------------

#: Where a material came from. A ``policy`` item is a formal enterprise original
#: and its versions are never directly editable; a ``generated_draft`` is agent
#: output and is excluded from formal retrieval until a separate publish
#: workflow approves it (``data-model.md`` Cross-Entity Invariant #9).
MATERIAL_SOURCE_TYPES: frozenset[str] = frozenset({"policy", "user_upload", "generated_draft"})

#: Material lifecycle -- this is the cross-store saga in ``tasks.md`` T083:
#: ``pending_upload -> scanning -> indexing -> available -> deleting ->
#: deleted/error``. It is deliberately *not* the same vocabulary as
#: ``MATERIAL_VERSION_STATUSES``: the material tracks the saga that is currently
#: running for it, a version tracks its own publication state.
MATERIAL_STATUSES: frozenset[str] = frozenset(
    {
        "pending_upload",
        "scanning",
        "indexing",
        "available",
        "deleting",
        "deleted",
        "error",
    }
)

#: Allowed material saga transitions. ``deleted`` is terminal because physical
#: deletion is irreversible; ``error`` is a parking state the saga can resume
#: from, so a transient object-store or Milvus fault is never fatal.
MATERIAL_SAGA_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_upload": frozenset({"scanning", "deleting", "error"}),
    "scanning": frozenset({"indexing", "deleting", "error"}),
    "indexing": frozenset({"available", "deleting", "error"}),
    "available": frozenset({"indexing", "deleting", "error"}),
    "deleting": frozenset({"deleted", "error"}),
    "deleted": frozenset(),
    # Resume goes back to the step that failed; the saga decides which by
    # replaying its own progress, so every non-terminal state is reachable.
    "error": frozenset({"pending_upload", "scanning", "indexing", "available", "deleting"}),
}

#: MaterialVersion publication state, quoted verbatim from ``data-model.md``.
MATERIAL_VERSION_STATUSES: frozenset[str] = frozenset(
    {
        "staging",
        "scanning",
        "indexing",
        "available",
        "quarantined",
        "superseded",
        "deleting",
    }
)

#: Fields that are frozen once a version row exists. ``status`` is excluded on
#: purpose: it is the lifecycle column the saga advances. Everything else
#: describes the immutable bytes a completed run may already have cited as
#: evidence, so an edit must create a new version row instead.
MATERIAL_VERSION_PUBLISHED_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "material_id",
        "version_number",
        "source_version_id",
        "object_version_id",
        "sha256",
        "size_bytes",
        "media_type",
        "created_by",
        "created_at",
    }
)

#: Malware/content scan outcome for a stored object version.
OBJECT_SCAN_STATUSES: frozenset[str] = frozenset(
    {"pending", "scanning", "clean", "infected", "failed"}
)
#: Retention/deletion state of a stored object version. ``deleting`` is the
#: recoverable middle state physical deletion parks in.
OBJECT_DELETION_STATES: frozenset[str] = frozenset({"retained", "deleting", "deleted"})

#: EmbeddingVersion lifecycle, quoted verbatim from ``data-model.md``.
EMBEDDING_VERSION_STATUSES: frozenset[str] = frozenset({"building", "active", "retired"})

#: Deletion state of a vector manifest, mirroring the object-store vocabulary.
VECTOR_DELETION_STATES: frozenset[str] = frozenset({"retained", "deleting", "deleted"})

#: Which two stores a reconciliation issue compares. The *direction* is carried
#: by the issue kind (``missing_*`` vs ``orphan_*``), so the pair is unordered.
RECONCILIATION_STORE_PAIRS: frozenset[str] = frozenset(
    {"postgres_object", "postgres_milvus", "object_milvus"}
)
#: Issue kinds, quoted verbatim from ``data-model.md``.
RECONCILIATION_ISSUE_KINDS: frozenset[str] = frozenset(
    {
        "missing_object",
        "orphan_object",
        "missing_vector",
        "orphan_vector",
        "missing_chunk",
        "version_drift",
    }
)
RECONCILIATION_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "critical"})
#: Issue lifecycle. An issue ends either repaired automatically or escalated to
#: a human; it is never silently dropped.
RECONCILIATION_STATES: frozenset[str] = frozenset(
    {"open", "repairing", "repaired", "manual_required"}
)
RECONCILIATION_TERMINAL_STATES: frozenset[str] = frozenset({"repaired", "manual_required"})

#: Sentinel for "this issue is not about one specific version". A real NULL
#: cannot be used: PostgreSQL treats NULLs as distinct in a unique constraint,
#: which would let a periodic sweep insert a duplicate row every pass.
NO_VERSION_SENTINEL: Final = ""

# --- Stage 6: workspaces, change sets, approvals and submissions ------------

#: TaskWorkspace lifecycle, quoted verbatim from ``data-model.md``.
WORKSPACE_STATUSES: frozenset[str] = frozenset(
    {
        "provisioning",
        "ready",
        "processing",
        "changes_ready",
        "awaiting_approval",
        "closed",
        "expired",
        "failed",
    }
)

#: Why a material version was pulled into a workspace. A ``read`` input is
#: reference-only; an ``edit`` input may be the source of a proposed change. A
#: formal policy original is always ``read`` (enforced by ``WorkspaceInput``'s
#: ``read_only`` flag), so it can never become a writable workspace output.
WORKSPACE_INPUT_PURPOSES: frozenset[str] = frozenset({"read", "edit"})

#: ChangeSet state, quoted verbatim from ``data-model.md``.
CHANGE_SET_STATES: frozenset[str] = frozenset(
    {
        "draft",
        "ready",
        "awaiting_approval",
        "approved",
        "rejected",
        "invalidated",
        "applied",
    }
)

#: What class of side effect applying a change set would cause. The approval
#: UI and the submission path both read this: ``external_submission`` is the only
#: class that leaves the system, and it is the one that requires a connector.
CHANGE_SET_SIDE_EFFECT_CLASSES: frozenset[str] = frozenset(
    {"none", "internal_write", "external_submission"}
)

#: What a change-set item does to one path.
CHANGE_ITEM_OPERATIONS: frozenset[str] = frozenset({"create", "update", "delete"})

#: ApprovalRequest status, quoted verbatim from ``data-model.md``.
APPROVAL_STATUSES: frozenset[str] = frozenset(
    {"pending", "approved", "rejected", "expired", "invalidated", "consumed"}
)

#: Allowed approval transitions. ``pending`` is the only state a decision may be
#: made from; ``approved`` is consumed exactly once (atomically with claiming the
#: submission) or expires/invalidates. Everything else is terminal.
APPROVAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "rejected", "expired", "invalidated"}),
    "approved": frozenset({"consumed", "invalidated", "expired"}),
    "rejected": frozenset(),
    "expired": frozenset(),
    "invalidated": frozenset(),
    "consumed": frozenset(),
}

#: The inputs an action digest binds, and therefore the set whose change
#: invalidates a prior approval (``data-model.md`` ChangeSet validation). Shared
#: by the digest (``approvals/digest.py``) and the approval service so they can
#: never disagree about what "the same approval" means.
APPROVAL_DIGEST_INPUTS: frozenset[str] = frozenset(
    {
        "action",
        "destination",
        "source_versions",
        "output_versions",
        "file_hashes",
        "diff",
        "evidence_set",
        "permission_snapshot",
        "side_effects",
    }
)

#: SubmissionJob state, quoted verbatim from ``data-model.md``.
SUBMISSION_STATES: frozenset[str] = frozenset(
    {
        "ready",
        "executing",
        "succeeded",
        "recoverable_failed",
        "cancelled",
        "terminal_failed",
        "unknown_outcome",
        "reconciling",
    }
)

#: Allowed submission transitions. The load-bearing rule is that an
#: ``unknown_outcome`` (the provider accepted the request but its result is
#: unknown) can only reach ``ready`` *through* ``reconciling`` -- never directly
#: -- so a non-idempotent side effect is never blindly retried.
SUBMISSION_TRANSITIONS: dict[str, frozenset[str]] = {
    "ready": frozenset({"executing", "cancelled", "terminal_failed"}),
    "executing": frozenset(
        {"succeeded", "recoverable_failed", "cancelled", "terminal_failed", "unknown_outcome"}
    ),
    "recoverable_failed": frozenset({"ready"}),
    "unknown_outcome": frozenset({"reconciling"}),
    "reconciling": frozenset({"succeeded", "ready", "terminal_failed"}),
    "succeeded": frozenset(),
    "cancelled": frozenset(),
    "terminal_failed": frozenset(),
}


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


class DurableJob(SQLModel, table=True):
    """PostgreSQL-authoritative unit of durable background work.

    RabbitMQ only *wakes* a worker; this row is the source of truth for whether
    work is owed, owned or done. State transitions are compare-and-set on
    ``version`` so a redelivered message or a second worker can never advance a
    job twice. ``idempotency_key`` is unique per ``(tenant, kind)`` so enqueuing
    the same logical work twice returns the first job instead of creating a
    duplicate. Payload never carries secrets or file bytes.
    """

    __tablename__ = "durable_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "idempotency_key", name="uq_durable_jobs_idem"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str | None = Field(default=None, index=True, max_length=36)
    kind: str = Field(index=True, max_length=60)
    idempotency_key: str = Field(index=True, max_length=128)
    payload_schema_version: int = Field(default=1, ge=1)
    payload_digest: str = Field(max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    state: str = Field(default="queued", index=True, max_length=20)
    priority_lane: str = Field(default="default", index=True, max_length=40)
    lease_owner: str | None = Field(default=None, max_length=64)
    lease_expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    heartbeat_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    available_at: datetime = Field(default_factory=utc_now, index=True, sa_type=UTCDateTime)
    deadline_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    result_ref: str | None = Field(default=None, max_length=256)
    last_error_code: str | None = Field(default=None, max_length=80)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class OutboxEvent(SQLModel, table=True):
    """Transactional-outbox row written in the same transaction as a job change.

    The unique ``(aggregate_type, aggregate_id, aggregate_version, event_type)``
    constraint is the idempotent-publication guarantee: a transition that has
    already produced its outbox row cannot produce a second one, so a retried
    business operation does not double-publish. The publisher claims rows, emits
    them to RabbitMQ with publisher confirms, and only then marks ``delivered``.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            name="uq_outbox_aggregate_version_event",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    aggregate_type: str = Field(index=True, max_length=60)
    aggregate_id: str = Field(index=True, max_length=36)
    aggregate_version: int = Field(ge=1)
    event_type: str = Field(index=True, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    delivery_state: str = Field(default="pending", index=True, max_length=20)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=8, ge=1)
    available_at: datetime = Field(default_factory=utc_now, index=True, sa_type=UTCDateTime)
    delivered_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    last_error_code: str | None = Field(default=None, max_length=80)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class QuotaPolicy(SQLModel, table=True):
    """Versioned admission limits for a scope (global / tenant / user).

    Redis performs the atomic per-request admission decision, but PostgreSQL is
    the authority for the policy itself: the numbers here are what the Lua token
    buckets and lease semaphores are seeded from. A global policy carries a null
    ``tenant_id``; only one row per ``(scope, tenant, workload)`` is ``active``.
    """

    __tablename__ = "quota_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope", "tenant_id", "workload", "version", name="uq_quota_policy_version"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    scope: str = Field(index=True, max_length=20)
    tenant_id: str | None = Field(default=None, foreign_key="tenants.id", index=True, max_length=36)
    workload: str | None = Field(default=None, index=True, max_length=40)
    requests_per_window: int = Field(default=0, ge=0)
    window_seconds: int = Field(default=60, ge=1)
    tokens_per_window: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=0, ge=0)
    queue_admission_limit: int = Field(default=0, ge=0)
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class QuotaLease(SQLModel, table=True):
    """Durable audit correlate of a short-lived Redis concurrency lease.

    Redis holds the live semaphore slot; this row records that a slot was taken,
    by whom, for which resource, and how it ended. Lease expiry is recorded here
    but is *not* permission to duplicate a non-idempotent side effect — that
    remains gated by the DurableJob / idempotency-key machinery.
    """

    __tablename__ = "quota_leases"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str | None = Field(default=None, index=True, max_length=36)
    owner: str = Field(index=True, max_length=64)
    resource: str = Field(index=True, max_length=120)
    acquired_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    expires_at: datetime = Field(sa_type=UTCDateTime)
    released_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    outcome: str | None = Field(default=None, index=True, max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class UsageRecord(SQLModel, table=True):
    """Append-only actual/reserved usage tied to a run and tenant.

    There is no ``version`` and no ``updated_at``: usage is never edited, only
    appended. Reserved vs actual are kept apart so an over-reservation that is
    later trued-up does not corrupt the billed figure.
    """

    __tablename__ = "usage_records"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str | None = Field(default=None, index=True, max_length=36)
    user_id: str | None = Field(default=None, index=True, max_length=36)
    kind: str = Field(index=True, max_length=30)
    provider: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=80)
    requests: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)
    actual_tokens: int = Field(default=0, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    occurred_at: datetime = Field(default_factory=utc_now, index=True, sa_type=UTCDateTime)


class CapacityTestRun(SQLModel, table=True):
    """Immutable evidence for a capacity/load claim.

    A row without ``raw_artifact_sha256`` and complete environment metadata
    cannot back a capacity claim, and mock vs real-provider results are kept in
    separate rows (``llm_mode``) so they are never merged. Not tenant-scoped: a
    capacity run measures the platform, not a customer.
    """

    __tablename__ = "capacity_test_runs"
    __table_args__ = (
        UniqueConstraint("raw_artifact_sha256", name="uq_capacity_raw_artifact"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    commit_sha: str = Field(index=True, max_length=40)
    scenario: str = Field(index=True, max_length=60)
    suite_version: str = Field(max_length=40)
    environment_manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    topology: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    hardware: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    dataset_manifest: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    llm_mode: str = Field(index=True, max_length=30)
    target_profile: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    started_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    duration_seconds: int = Field(default=0, ge=0)
    raw_artifact_uri: str = Field(max_length=512)
    raw_artifact_sha256: str = Field(max_length=64)
    summary_metrics: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    verdict: str = Field(default="inconclusive", index=True, max_length=20)
    known_limits: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


# --- Stage 5 entities -------------------------------------------------------


class Material(SQLModel, table=True):
    """A logical enterprise item: the stable identity an active version hangs off.

    ``active_version_id`` is a *pointer*, not a foreign key. A real FK here would
    be mutually dependent with ``material_versions.material_id``: PostgreSQL
    cannot create a cycle in one pass and SQLite cannot ``ALTER TABLE ... ADD
    CONSTRAINT`` to close it afterwards. The authority for "which version may be
    retrieved" is therefore the compare-and-set-activated ``VectorManifest``
    (one ``retrievable`` row per material), and reconciliation reports a pointer
    that disagrees with it as ``version_drift`` -- which is strictly better than
    an FK, because an FK could not have detected that class of drift at all.

    ``read_only`` marks a formal policy original: its versions never become
    writable workspace outputs (``data-model.md`` Cross-Entity Invariant #8).
    """

    __tablename__ = "materials"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    knowledge_base_id: str | None = Field(
        default=None, foreign_key="knowledge_bases.id", index=True, max_length=36
    )
    owner_user_id: str | None = Field(
        default=None, foreign_key="users.id", index=True, max_length=36
    )
    name: str = Field(max_length=255)
    source_type: str = Field(index=True, max_length=30)
    status: str = Field(default="pending_upload", index=True, max_length=20)
    active_version_id: str | None = Field(default=None, index=True, max_length=36)
    read_only: bool = Field(default=False)
    # Recovery fields required of every recoverable step (``data-model.md``
    # Modeling Rules): the material *is* the saga's recoverable unit, so the
    # attempt budget for its current cross-store step lives here. It is reset when
    # the saga moves to a new step, so a later version does not inherit an earlier
    # version's failures.
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    next_attempt_at: datetime | None = Field(default=None, index=True, sa_type=UTCDateTime)
    last_error_code: str | None = Field(default=None, max_length=80)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class ObjectVersion(SQLModel, table=True):
    """One immutable provider object version in the versioned object store.

    The row records the *opaque* key the service derived plus the provider's own
    ``version_id``; API clients never choose or see either (they address material
    and version IDs). ``material_version_id`` is a plain column rather than a
    foreign key for the same cycle reason as ``Material.active_version_id``: the
    authoritative link is ``material_versions.object_version_id``, and a
    disagreement between the two directions is exactly what the
    ``missing_object`` / ``orphan_object`` reconciliation checks look for.
    """

    __tablename__ = "object_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "bucket_alias",
            "object_key",
            "provider_version_id",
            name="uq_object_versions_provider",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    material_id: str = Field(foreign_key="materials.id", index=True, max_length=36)
    material_version_id: str | None = Field(default=None, index=True, max_length=36)
    bucket_alias: str = Field(max_length=63)
    object_key: str = Field(index=True, max_length=512)
    provider_version_id: str = Field(max_length=256)
    sha256: str = Field(max_length=64)
    size_bytes: int = Field(ge=0)
    media_type: str = Field(max_length=180)
    encryption_algorithm: str = Field(default="AES256", max_length=40)
    encryption_key_id: str | None = Field(default=None, max_length=256)
    scan_status: str = Field(default="pending", index=True, max_length=20)
    deletion_state: str = Field(default="retained", index=True, max_length=20)
    retention_until: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class MaterialVersion(SQLModel, table=True):
    """An immutable version of a material; editing appends, never mutates.

    ``version_number`` is unique per material and monotonically increasing.
    Only the root (``version_number == 1``) may omit ``source_version_id``; a
    later version without a parent would fork the chain invisibly, which the
    ``ck_material_versions_root_chain`` CHECK and
    :func:`material_version_chain_error` both reject.

    ``sha256`` / ``size_bytes`` / ``media_type`` restate the object's metadata so
    the database can be checked against the bytes; :func:`object_metadata_error`
    is the single place that comparison lives.
    """

    __tablename__ = "material_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "material_id", "version_number", name="uq_material_versions_number"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    material_id: str = Field(foreign_key="materials.id", index=True, max_length=36)
    version_number: int = Field(ge=1, index=True)
    source_version_id: str | None = Field(
        default=None, foreign_key="material_versions.id", index=True, max_length=36
    )
    object_version_id: str | None = Field(
        default=None, foreign_key="object_versions.id", index=True, max_length=36
    )
    sha256: str = Field(max_length=64)
    size_bytes: int = Field(ge=0)
    media_type: str = Field(max_length=180)
    status: str = Field(default="staging", index=True, max_length=20)
    created_by: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class EmbeddingVersion(SQLModel, table=True):
    """The retrieval contract a set of vectors was produced under.

    Exactly one row per ``(tenant, knowledge base, cohort)`` may be ``active``,
    enforced by the partial unique index ``uq_embedding_versions_active`` rather
    than by convention: two simultaneously-active embedding versions would mean
    a query mixes vector spaces, which silently destroys ranking.
    """

    __tablename__ = "embedding_versions"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    knowledge_base_id: str = Field(
        foreign_key="knowledge_bases.id", index=True, max_length=36
    )
    cohort: str = Field(default="default", index=True, max_length=60)
    provider: str = Field(max_length=60)
    model_identifier: str = Field(max_length=200)
    dimensions: int = Field(ge=1)
    normalization: str = Field(default="l2", max_length=20)
    chunking_policy_version: str = Field(default="1", max_length=20)
    status: str = Field(default="building", index=True, max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class VectorManifest(SQLModel, table=True):
    """The PostgreSQL-side record of what a material/document owns in Milvus.

    ``retrievable`` is the switch a query filters on, and at most one manifest
    per subject may carry it (``uq_vector_manifests_active_material`` /
    ``..._document``). Activation is therefore a compare-and-set: the new
    manifest is staged and verified first, and only the flag flip makes it
    authoritative, so the previous version keeps serving until that instant and
    the two are never simultaneously authoritative.

    A manifest describes a material version *or* a knowledge document, never
    both and never neither (``ck_vector_manifests_one_subject``).
    """

    __tablename__ = "vector_manifests"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    knowledge_base_id: str = Field(
        foreign_key="knowledge_bases.id", index=True, max_length=36
    )
    material_id: str | None = Field(
        default=None, foreign_key="materials.id", index=True, max_length=36
    )
    material_version_id: str | None = Field(
        default=None, foreign_key="material_versions.id", index=True, max_length=36
    )
    document_id: str | None = Field(
        default=None, foreign_key="knowledge_documents.id", index=True, max_length=36
    )
    embedding_version_id: str = Field(
        foreign_key="embedding_versions.id", index=True, max_length=36
    )
    milvus_database: str = Field(max_length=120)
    milvus_collection: str = Field(max_length=200)
    vector_id_prefix: str = Field(index=True, max_length=200)
    chunk_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    expected_count: int = Field(default=0, ge=0)
    indexed_count: int = Field(default=0, ge=0)
    content_hash: str = Field(default="", max_length=64)
    retrievable: bool = Field(default=False, index=True)
    activated_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    deletion_state: str = Field(default="retained", index=True, max_length=20)
    deleted_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class ReconciliationIssue(SQLModel, table=True):
    """One detected disagreement between two stores.

    The natural key ``(tenant, store_pair, issue_kind, resource_kind,
    resource_id, version_id)`` is unique so a periodic sweep *re-finds* an open
    issue instead of inserting a duplicate on every pass -- which is what makes
    "100% detection" a stable number rather than a growing pile. ``version_id``
    is NOT NULL with the :data:`NO_VERSION_SENTINEL` empty string for the same
    reason: PostgreSQL treats NULLs as distinct in a unique constraint.
    """

    __tablename__ = "reconciliation_issues"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "store_pair",
            "issue_kind",
            "resource_kind",
            "resource_id",
            "version_id",
            name="uq_reconciliation_issues_natural",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    store_pair: str = Field(index=True, max_length=30)
    issue_kind: str = Field(index=True, max_length=30)
    resource_kind: str = Field(max_length=60)
    resource_id: str = Field(index=True, max_length=256)
    version_id: str = Field(default=NO_VERSION_SENTINEL, max_length=256)
    observed_fingerprint: str | None = Field(default=None, max_length=256)
    expected_fingerprint: str | None = Field(default=None, max_length=256)
    severity: str = Field(default="warning", index=True, max_length=20)
    state: str = Field(default="open", index=True, max_length=20)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    next_attempt_at: datetime | None = Field(default=None, index=True, sa_type=UTCDateTime)
    last_error_code: str | None = Field(default=None, max_length=80)
    resolution: str | None = Field(default=None, max_length=500)
    detected_at: datetime = Field(default_factory=utc_now, index=True, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    resolved_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


# --- Stage 5 invariant helpers ----------------------------------------------


def next_version_number(existing: Iterable[int]) -> int:
    """Return the next monotonic ``version_number`` after ``existing``.

    Monotonic rather than "count + 1": a gap (a superseded version that was
    physically deleted) must never let a new version reuse a smaller number,
    because an evidence row may still cite the larger one.
    """
    numbers = list(existing)
    return (max(numbers) + 1) if numbers else 1


def material_version_chain_error(
    *, version_number: int, source_version_id: str | None
) -> str | None:
    """Validate the root/parent rule; return a reason or ``None`` when valid."""
    if version_number == 1 and source_version_id is not None:
        return "the root version (version_number=1) cannot declare source_version_id"
    if version_number > 1 and source_version_id is None:
        return f"version_number={version_number} requires source_version_id"
    return None


def object_metadata_error(
    version: "MaterialVersion", object_version: "ObjectVersion"
) -> str | None:
    """Compare a version's claim about the bytes with the object's own metadata.

    Returns a reason naming the first disagreeing field, or ``None`` when the
    database and the object store agree. Tenant ownership is checked first: a
    version must never point at another tenant's object, and that is a harder
    failure than a hash mismatch.
    """
    if version.tenant_id != object_version.tenant_id:
        return (
            "tenant_id mismatch between material version and object version; "
            "cross-tenant object references are forbidden"
        )
    if version.material_id != object_version.material_id:
        return "material_id mismatch between material version and object version"
    for field in ("sha256", "size_bytes", "media_type"):
        claimed = getattr(version, field)
        actual = getattr(object_version, field)
        if claimed != actual:
            return f"{field} mismatch: row claims {claimed!r}, object reports {actual!r}"
    return None


def published_field_violations(
    before: "MaterialVersion", after: "MaterialVersion"
) -> list[str]:
    """Return the published fields ``after`` changed relative to ``before``.

    An empty list means the write only touched mutable lifecycle columns. A
    non-empty list must abort the write: a published version is evidence, and
    rewriting it in place would retroactively change what a completed run cited.
    """
    return sorted(
        field
        for field in MATERIAL_VERSION_PUBLISHED_FIELDS
        if getattr(before, field, None) != getattr(after, field, None)
    )


# --- Stage 6 entities -------------------------------------------------------


class TaskWorkspace(SQLModel, table=True):
    """A short-lived, tenant-owned editing context bound to one run.

    Ownership (tenant/run/user/session) is fixed at creation. ``sandbox_job_ref``
    is an *opaque* infrastructure reference (a Kubernetes Job name, say), never a
    host path, and the API layer never surfaces it. ``input_manifest_digest``
    pins the exact selected versions, and ``policy_snapshot`` the resource policy
    in force, so what the sandbox was allowed to see is itself auditable.
    """

    __tablename__ = "task_workspaces"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str = Field(index=True, max_length=36)
    user_id: str = Field(foreign_key="users.id", index=True, max_length=36)
    session_id: str = Field(default="", max_length=64)
    status: str = Field(default="provisioning", index=True, max_length=20)
    sandbox_job_ref: str | None = Field(default=None, max_length=200)
    input_manifest_digest: str = Field(default="", max_length=64)
    policy_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    expires_at: datetime | None = Field(default=None, index=True, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    closed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class WorkspaceInput(SQLModel, table=True):
    """An explicitly selected material version joined into a workspace.

    This join is the *only* way a version enters a workspace: it cannot be
    expanded by model output (``data-model.md``). ``read_only`` is NOT NULL and is
    forced true for a formal policy original, so the "originals never become
    writable outputs" invariant does not depend on a tri-state.
    """

    __tablename__ = "workspace_inputs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "material_version_id", name="uq_workspace_inputs_version"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    workspace_id: str = Field(foreign_key="task_workspaces.id", index=True, max_length=36)
    material_version_id: str = Field(
        foreign_key="material_versions.id", index=True, max_length=36
    )
    purpose: str = Field(default="read", max_length=20)
    staged_hash: str = Field(default="", max_length=64)
    read_only: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class ChangeSet(SQLModel, table=True):
    """A proposed set of edits, with the digests an approval binds to.

    ``source_manifest_digest`` and ``evidence_set_digest`` pin what the change was
    computed from; ``side_effect_class`` says whether applying it leaves the
    system. Any change to an input, output, destination, evidence set or
    permission snapshot invalidates a prior approval, which the approval service
    enforces by recomputing the action digest.
    """

    __tablename__ = "change_sets"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    workspace_id: str = Field(foreign_key="task_workspaces.id", index=True, max_length=36)
    run_id: str = Field(index=True, max_length=36)
    source_manifest_digest: str = Field(default="", max_length=64)
    evidence_set_digest: str = Field(default="", max_length=64)
    summary: str = Field(default="", max_length=2000)
    side_effect_class: str = Field(default="none", index=True, max_length=30)
    state: str = Field(default="draft", index=True, max_length=20)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class ChangeSetItem(SQLModel, table=True):
    """One edit to one path, with before/after hashes and the diff artifact.

    ``normalized_path`` is normalised before persistence and is unique within the
    workspace, so two items can never target the same file -- the first guard in
    the traversal defence (``data-model.md``). ``source_version_id`` is required
    for an update/delete and absent for a create (``change_item_source_error``).
    """

    __tablename__ = "change_set_items"
    __table_args__ = (
        # Unique per *change set*, not per workspace: a workspace may legitimately
        # hold more than one change set over its lifetime (a rejected draft, then a
        # revised one), each targeting the same file. Within one change set a path
        # appears exactly once, so two items can never target the same file.
        UniqueConstraint(
            "change_set_id", "normalized_path", name="uq_change_set_items_path"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    change_set_id: str = Field(foreign_key="change_sets.id", index=True, max_length=36)
    workspace_id: str = Field(foreign_key="task_workspaces.id", index=True, max_length=36)
    source_version_id: str | None = Field(
        default=None, foreign_key="material_versions.id", max_length=36
    )
    proposed_version_id: str | None = Field(
        default=None, foreign_key="material_versions.id", max_length=36
    )
    operation: str = Field(max_length=20)
    normalized_path: str = Field(index=True, max_length=1024)
    before_hash: str | None = Field(default=None, max_length=64)
    after_hash: str | None = Field(default=None, max_length=64)
    diff_artifact_ref: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class ApprovalRequest(SQLModel, table=True):
    """One immutable human-review target for one change set.

    ``action_digest`` is the SHA-256 over everything the decision is about
    (action, destination, exact files/hashes, source/output versions, diff,
    evidence and permission snapshot); an approval can never be reused for a
    different digest. ``authorization_version`` is a snapshot for explanation --
    the *current* authorization is still rechecked at execution time.
    """

    __tablename__ = "approval_requests"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str = Field(index=True, max_length=36)
    change_set_id: str = Field(foreign_key="change_sets.id", index=True, max_length=36)
    action: str = Field(max_length=60)
    destination: str = Field(default="", max_length=200)
    action_digest: str = Field(index=True, max_length=64)
    requested_by: str = Field(foreign_key="users.id", max_length=36)
    decided_by: str | None = Field(default=None, foreign_key="users.id", max_length=36)
    authorization_version: int = Field(default=1, ge=1)
    status: str = Field(default="pending", index=True, max_length=20)
    expires_at: datetime | None = Field(default=None, index=True, sa_type=UTCDateTime)
    decided_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    reason: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


class SubmissionJob(SQLModel, table=True):
    """A unique external-submission execution record.

    The unique ``(tenant_id, connector_id, idempotency_key)`` is the whole
    at-most-one-accepted-action guarantee: a duplicate click, a client retry or a
    worker restart all resolve to the same row. An ``unknown_outcome`` must be
    reconciled before the job can retry, so a non-idempotent provider action is
    never issued twice.
    """

    __tablename__ = "submission_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "connector_id", "idempotency_key", name="uq_submission_jobs_idem"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    tenant_id: str = Field(foreign_key="tenants.id", index=True, max_length=36)
    run_id: str = Field(index=True, max_length=36)
    approval_id: str = Field(foreign_key="approval_requests.id", index=True, max_length=36)
    connector_id: str = Field(index=True, max_length=60)
    destination: str = Field(default="", max_length=200)
    idempotency_key: str = Field(index=True, max_length=128)
    expected_target_version: str | None = Field(default=None, max_length=36)
    state: str = Field(default="ready", index=True, max_length=20)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    provider_receipt: str | None = Field(default=None, max_length=256)
    sanitized_result: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    next_attempt_at: datetime | None = Field(default=None, index=True, sa_type=UTCDateTime)
    last_error_code: str | None = Field(default=None, max_length=80)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    version: int = Field(default=1, ge=1)


def change_item_source_error(
    *, operation: str, source_version_id: str | None
) -> str | None:
    """Validate the operation/source-version rule; return a reason or ``None``.

    A ``create`` introduces a new path and must not claim a source version; an
    ``update`` or ``delete`` acts on an existing version and must name it, both so
    the diff has a baseline and so a concurrent edit can be detected by expected
    version (``data-model.md``: mismatch produces conflict, never silent
    overwrite).
    """
    if operation == "create" and source_version_id is not None:
        return "a create operation cannot declare a source_version_id"
    if operation in {"update", "delete"} and source_version_id is None:
        return f"a {operation} operation requires a source_version_id"
    return None


