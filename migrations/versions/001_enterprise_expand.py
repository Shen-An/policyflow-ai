"""enterprise expand

Migration phase: expand
Revision ID: 001
Revises:
Create Date: 2026-09-14 13:53:19.323641

Stage 2 expand migration. It is purely additive on an existing database: every
legacy table keeps working while ``tenant_id`` exists as a NULLABLE column, the
``legacy`` tenant and memberships are seeded, and RLS policies exist but are not
yet forced. ``002_enterprise_enforce`` turns ownership into a hard constraint
only after the backfill has been reconciled.

Generate revisions with an explicit phase, for example:
    alembic -x phase=expand revision -m "add enterprise tables"
Run a constrained upgrade with:
    alembic -x phase=expand upgrade head
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

import backend.app.db.models
from migrations.phases import validate_migration_phase

revision: str = '001'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ('phase:expand:001',)
depends_on: str | Sequence[str] | None = None
migration_phase = validate_migration_phase(
    'expand', revision
)

# Stable identifier for the tenant that owns every pre-tenant row.
LEGACY_TENANT_ID = "00000000-0000-0000-0000-000000000001"
LEGACY_TENANT_CODE = "legacy"

# Tables that carry tenant ownership and therefore receive a per-tenant RLS
# policy. Policies are created here but ENABLE/FORCE happens in 002 so that the
# nullable-ownership window can still be backfilled.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "users",
    "roles",
    "user_role_grants",
    "knowledge_bases",
    "knowledge_documents",
    "conversations",
    "messages",
    "memory_items",
    "drafts",
    "agent_runs",
    "run_events",
    "graph_checkpoint_bindings",
    "idempotency_records",
    "audit_events",
)

# Enum-like columns enforced as CHECK constraints. Keeping them in the database
# makes an invalid state impossible even for a caller that bypasses the ORM.
CHECK_CONSTRAINTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("tenants", "ck_tenants_status", "status", ("active", "suspended", "deleting")),
    ("users", "ck_users_status", "status", ("active", "suspended", "disabled")),
    (
        "agent_runs",
        "ck_agent_runs_kind",
        "kind",
        ("chat", "eval", "file_workflow", "reconciliation"),
    ),
    (
        "agent_runs",
        "ck_agent_runs_status",
        "status",
        (
            "queued",
            "running",
            "waiting_approval",
            "cancel_requested",
            "cancelled",
            "succeeded",
            "recoverable_failed",
            "terminal_failed",
            "timed_out",
        ),
    ),
    (
        "agent_runs",
        "ck_agent_runs_evidence_gate",
        "evidence_gate",
        ("supported", "insufficient_evidence", "not_applicable"),
    ),
    (
        "idempotency_records",
        "ck_idempotency_records_state",
        "state",
        ("in_progress", "completed", "failed"),
    ),
    (
        "audit_events",
        "ck_audit_events_outcome",
        "outcome",
        ("allowed", "denied", "succeeded", "failed"),
    ),
)


def _quote(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _add_check_constraints() -> None:
    """Constrain enum-like columns in the database, not only in Python.

    PostgreSQL is the only production SQL authority, so the constraints are
    added with ``ALTER TABLE ... ADD CONSTRAINT`` there. SQLite (development and
    isolated tests only) cannot ALTER constraints without a table rebuild, so
    this step is skipped for that dialect rather than silently emulated; the
    contract suite exercises these constraints against PostgreSQL.
    """
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, name, column, allowed in CHECK_CONSTRAINTS:
        op.create_check_constraint(name, table, f"{column} IN ({_quote(allowed)})")


def _seed_legacy_tenant() -> None:
    """Create the ``legacy`` tenant that owns every pre-tenant row.

    Every existing user becomes an active member of it, which is what makes the
    backfill in ``migrations/backfill_legacy_tenant.py`` able to attribute old
    conversations, memory, knowledge and eval records to a real tenant.

    This step is PostgreSQL-only: it uses ``now()``, ``md5`` and ``::uuid``
    casts. SQLite is a development convenience that bootstraps its schema with
    ``create_all``, never through this migration.
    """
    if op.get_bind().dialect.name != "postgresql":
        return
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO tenants (id, code, name, status, created_at, updated_at)
            VALUES (:id, :code, :name, 'active', now(), now())
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {"id": LEGACY_TENANT_ID, "code": LEGACY_TENANT_CODE, "name": "Legacy tenant"},
    )
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET tenant_id = :tenant_id
            WHERE tenant_id IS NULL
            """
        ),
        {"tenant_id": LEGACY_TENANT_ID},
    )
    bind.execute(
        sa.text(
            """
            UPDATE roles
            SET tenant_id = :tenant_id
            WHERE tenant_id IS NULL
            """
        ),
        {"tenant_id": LEGACY_TENANT_ID},
    )
    # Deterministic memberships: existing user/role pairs are re-expressed as
    # explicit grants with an unbounded validity window.
    bind.execute(
        sa.text(
            """
            INSERT INTO user_role_grants (
                id, tenant_id, user_id, role_id, scope, valid_from,
                granted_by, version, created_at, updated_at
            )
            SELECT
                md5(ur.user_id || ':' || ur.role_id)::uuid::text,
                :tenant_id,
                ur.user_id,
                ur.role_id,
                'tenant',
                now(),
                NULL,
                1,
                now(),
                now()
            FROM user_roles AS ur
            JOIN users AS u ON u.id = ur.user_id
            JOIN roles AS r ON r.id = ur.role_id
            ON CONFLICT (tenant_id, user_id, role_id, scope) DO NOTHING
            """
        ),
        {"tenant_id": LEGACY_TENANT_ID},
    )


def _create_tenant_isolation_policies() -> None:
    """Add per-tenant RLS policies as defence in depth.

    Application authorization remains the primary control: every repository
    call takes an explicit tenant. RLS exists so a missed predicate cannot leak
    another tenant's rows. ``NULLIF(current_setting(...), '')`` keeps the policy
    inert (no rows) when the session has not set a tenant, which is the safe
    default for a connection that is not serving a request.

    SQLite (development and isolated tests only) has no row-level security, so
    this step is skipped for that dialect instead of being emulated.
    """
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id::text = NULLIF(current_setting('policyflow.tenant_id', true), ''))
            WITH CHECK (
                tenant_id::text = NULLIF(current_setting('policyflow.tenant_id', true), '')
            )
            """
        )


def upgrade() -> None:
    """Upgrade schema within the declared migration phase."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('audit_logs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('actor_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('action', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('target_type', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('target_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('ip_address', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_actor_id'), 'audit_logs', ['actor_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_request_id'), 'audit_logs', ['request_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_target_id'), 'audit_logs', ['target_id'], unique=False)
    op.create_table('departments',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('code', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('parent_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['departments.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_departments_code'), 'departments', ['code'], unique=True)
    op.create_table('eval_cases',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('question', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('expected_answer_keywords', sa.JSON(), nullable=False),
    sa.Column('expected_source_documents', sa.JSON(), nullable=False),
    sa.Column('expected_chunk_ids', sa.JSON(), nullable=False),
    sa.Column('should_answer', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_cases_category'), 'eval_cases', ['category'], unique=False)
    op.create_table('mcp_servers',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('server_type', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('integration_mode', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('endpoint', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
    sa.Column('command', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('health_status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('tools', sa.JSON(), nullable=False),
    sa.Column('last_error_code', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
    sa.Column('last_error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_checked_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mcp_servers_health_status'), 'mcp_servers', ['health_status'], unique=False)
    op.create_index(op.f('ix_mcp_servers_integration_mode'), 'mcp_servers', ['integration_mode'], unique=False)
    op.create_index(op.f('ix_mcp_servers_name'), 'mcp_servers', ['name'], unique=True)
    op.create_index(op.f('ix_mcp_servers_server_type'), 'mcp_servers', ['server_type'], unique=False)
    op.create_table('model_providers',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('provider_type', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('capability', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
    sa.Column('api_key_env', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('api_key_ciphertext', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('default_chat_model', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('default_embedding_model', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('config_json', sa.JSON(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_providers_capability'), 'model_providers', ['capability'], unique=False)
    op.create_index(op.f('ix_model_providers_name'), 'model_providers', ['name'], unique=True)
    op.create_table('retrieval_eval_items',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('eval_case_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('query', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('knowledge_base_ids', sa.JSON(), nullable=False),
    sa.Column('relevant_document_ids', sa.JSON(), nullable=False),
    sa.Column('relevant_chunk_ids', sa.JSON(), nullable=False),
    sa.Column('relevance_judgement', sa.JSON(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_retrieval_eval_items_eval_case_id'), 'retrieval_eval_items', ['eval_case_id'], unique=False)
    op.create_table('skills',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('version', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('risk_level', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_skills_name'), 'skills', ['name'], unique=True)
    op.create_table('tenants',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('code', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('resource_policy_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenants_code'), 'tenants', ['code'], unique=True)
    op.create_index(op.f('ix_tenants_status'), 'tenants', ['status'], unique=False)
    op.create_table('tool_call_logs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('agent_name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('tool_name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.Column('input_summary', sa.JSON(), nullable=False),
    sa.Column('output_summary', sa.JSON(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_call_logs_conversation_id'), 'tool_call_logs', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_tool_call_logs_request_id'), 'tool_call_logs', ['request_id'], unique=False)
    op.create_index(op.f('ix_tool_call_logs_status'), 'tool_call_logs', ['status'], unique=False)
    op.create_index(op.f('ix_tool_call_logs_tool_name'), 'tool_call_logs', ['tool_name'], unique=False)
    op.create_index(op.f('ix_tool_call_logs_user_id'), 'tool_call_logs', ['user_id'], unique=False)
    op.create_table('tools',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('input_schema', sa.JSON(), nullable=False),
    sa.Column('output_schema', sa.JSON(), nullable=False),
    sa.Column('risk_level', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tools_name'), 'tools', ['name'], unique=True)
    op.create_table('audit_events',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('event_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.Column('trace_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('actor_ref', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('authorization_version', sa.Integer(), nullable=False),
    sa.Column('action', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('resource_kind', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
    sa.Column('resource_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('outcome', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('reason_code', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=True),
    sa.Column('redacted_metadata', sa.JSON(), nullable=False),
    sa.Column('occurred_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_action'), 'audit_events', ['action'], unique=False)
    op.create_index(op.f('ix_audit_events_event_id'), 'audit_events', ['event_id'], unique=True)
    op.create_index(op.f('ix_audit_events_outcome'), 'audit_events', ['outcome'], unique=False)
    op.create_index(op.f('ix_audit_events_request_id'), 'audit_events', ['request_id'], unique=False)
    op.create_index(op.f('ix_audit_events_run_id'), 'audit_events', ['run_id'], unique=False)
    op.create_index(op.f('ix_audit_events_tenant_id'), 'audit_events', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_audit_events_trace_id'), 'audit_events', ['trace_id'], unique=False)
    op.create_table('eval_runs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('total_cases', sa.Integer(), nullable=False),
    sa.Column('started_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('finished_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('config_snapshot', sa.JSON(), nullable=False),
    sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('error_summary', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_runs_request_id'), 'eval_runs', ['request_id'], unique=False)
    op.create_index(op.f('ix_eval_runs_status'), 'eval_runs', ['status'], unique=False)
    op.create_index(op.f('ix_eval_runs_tenant_id'), 'eval_runs', ['tenant_id'], unique=False)
    op.create_table('idempotency_records',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('operation', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('idempotency_key', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
    sa.Column('request_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', sa.JSON(), nullable=True),
    sa.Column('expires_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'operation', 'idempotency_key')
    )
    op.create_index(op.f('ix_idempotency_records_idempotency_key'), 'idempotency_records', ['idempotency_key'], unique=False)
    op.create_index(op.f('ix_idempotency_records_operation'), 'idempotency_records', ['operation'], unique=False)
    op.create_index(op.f('ix_idempotency_records_state'), 'idempotency_records', ['state'], unique=False)
    op.create_index(op.f('ix_idempotency_records_tenant_id'), 'idempotency_records', ['tenant_id'], unique=False)
    op.create_table('knowledge_bases',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('code', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('department_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('rag_workspace', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('default_query_mode', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_bases_code'), 'knowledge_bases', ['code'], unique=True)
    op.create_index(op.f('ix_knowledge_bases_department_id'), 'knowledge_bases', ['department_id'], unique=False)
    op.create_index(op.f('ix_knowledge_bases_status'), 'knowledge_bases', ['status'], unique=False)
    op.create_index(op.f('ix_knowledge_bases_tenant_id'), 'knowledge_bases', ['tenant_id'], unique=False)
    op.create_table('memory_items',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('owner_type', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('owner_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('memory_type', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('content', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('embedding', sa.JSON(), nullable=True),
    sa.Column('meta_json', sa.JSON(), nullable=False),
    sa.Column('expires_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memory_items_memory_type'), 'memory_items', ['memory_type'], unique=False)
    op.create_index(op.f('ix_memory_items_owner_id'), 'memory_items', ['owner_id'], unique=False)
    op.create_index(op.f('ix_memory_items_owner_type'), 'memory_items', ['owner_type'], unique=False)
    op.create_index(op.f('ix_memory_items_tenant_id'), 'memory_items', ['tenant_id'], unique=False)
    op.create_table('roles',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('code', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('actions', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'code')
    )
    op.create_index(op.f('ix_roles_code'), 'roles', ['code'], unique=False)
    op.create_index(op.f('ix_roles_tenant_id'), 'roles', ['tenant_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('external_subject', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
    sa.Column('username', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('department_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'external_subject')
    )
    op.create_index(op.f('ix_users_department_id'), 'users', ['department_id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_external_subject'), 'users', ['external_subject'], unique=False)
    op.create_index(op.f('ix_users_status'), 'users', ['status'], unique=False)
    op.create_index(op.f('ix_users_tenant_id'), 'users', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('conversations',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('channel', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_status'), 'conversations', ['status'], unique=False)
    op.create_index(op.f('ix_conversations_tenant_id'), 'conversations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_table('eval_results',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('eval_run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('eval_case_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('retrieval_eval_item_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('question', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('answer', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('retrieved_sources', sa.JSON(), nullable=False),
    sa.Column('retrieval_metrics', sa.JSON(), nullable=True),
    sa.Column('answer_metrics', sa.JSON(), nullable=True),
    sa.Column('ragas_metrics', sa.JSON(), nullable=True),
    sa.Column('type_statuses', sa.JSON(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_results_eval_case_id'), 'eval_results', ['eval_case_id'], unique=False)
    op.create_index(op.f('ix_eval_results_eval_run_id'), 'eval_results', ['eval_run_id'], unique=False)
    op.create_index(op.f('ix_eval_results_retrieval_eval_item_id'), 'eval_results', ['retrieval_eval_item_id'], unique=False)
    op.create_table('faq_drafts',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('knowledge_base_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('source_document_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('source_conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('question', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('answer', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
    sa.Column('generated_by', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('reviewer_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('review_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_faq_drafts_knowledge_base_id'), 'faq_drafts', ['knowledge_base_id'], unique=False)
    op.create_index(op.f('ix_faq_drafts_source_conversation_id'), 'faq_drafts', ['source_conversation_id'], unique=False)
    op.create_index(op.f('ix_faq_drafts_source_document_id'), 'faq_drafts', ['source_document_id'], unique=False)
    op.create_index(op.f('ix_faq_drafts_status'), 'faq_drafts', ['status'], unique=False)
    op.create_table('knowledge_base_permissions',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('knowledge_base_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('subject_type', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('subject_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('permission', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_base_permissions_knowledge_base_id'), 'knowledge_base_permissions', ['knowledge_base_id'], unique=False)
    op.create_index(op.f('ix_knowledge_base_permissions_subject_id'), 'knowledge_base_permissions', ['subject_id'], unique=False)
    op.create_index(op.f('ix_knowledge_base_permissions_subject_type'), 'knowledge_base_permissions', ['subject_type'], unique=False)
    op.create_table('knowledge_documents',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('knowledge_base_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('file_path', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
    sa.Column('file_type', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('content_text', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('content_hash', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
    sa.Column('external_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.Column('index_status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('index_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('source_version', sa.Integer(), nullable=False),
    sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_documents_content_hash'), 'knowledge_documents', ['content_hash'], unique=False)
    op.create_index(op.f('ix_knowledge_documents_external_id'), 'knowledge_documents', ['external_id'], unique=False)
    op.create_index(op.f('ix_knowledge_documents_index_status'), 'knowledge_documents', ['index_status'], unique=False)
    op.create_index(op.f('ix_knowledge_documents_knowledge_base_id'), 'knowledge_documents', ['knowledge_base_id'], unique=False)
    op.create_index(op.f('ix_knowledge_documents_tenant_id'), 'knowledge_documents', ['tenant_id'], unique=False)
    op.create_table('user_role_grants',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('role_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
    sa.Column('valid_from', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('expires_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('granted_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('revoked_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('revoked_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('revocation_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'user_id', 'role_id', 'scope')
    )
    op.create_index(op.f('ix_user_role_grants_role_id'), 'user_role_grants', ['role_id'], unique=False)
    op.create_index(op.f('ix_user_role_grants_tenant_id'), 'user_role_grants', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_user_role_grants_user_id'), 'user_role_grants', ['user_id'], unique=False)
    op.create_table('user_roles',
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('role_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'role_id')
    )
    op.create_table('agent_runs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
    sa.Column('thread_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
    sa.Column('input_snapshot', sa.JSON(), nullable=False),
    sa.Column('result_snapshot', sa.JSON(), nullable=False),
    sa.Column('evidence_gate', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
    sa.Column('deadline_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('tool_call_count', sa.Integer(), nullable=False),
    sa.Column('graph_version', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('started_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('finished_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'thread_id')
    )
    op.create_index(op.f('ix_agent_runs_conversation_id'), 'agent_runs', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_kind'), 'agent_runs', ['kind'], unique=False)
    op.create_index(op.f('ix_agent_runs_run_id'), 'agent_runs', ['run_id'], unique=True)
    op.create_index(op.f('ix_agent_runs_status'), 'agent_runs', ['status'], unique=False)
    op.create_index(op.f('ix_agent_runs_tenant_id'), 'agent_runs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_thread_id'), 'agent_runs', ['thread_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_user_id'), 'agent_runs', ['user_id'], unique=False)
    op.create_table('ai_query_logs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('question', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('answer', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('knowledge_base_ids', sa.JSON(), nullable=False),
    sa.Column('retrieved_sources', sa.JSON(), nullable=False),
    sa.Column('confidence_score', sa.Float(), nullable=False),
    sa.Column('query_mode', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('token_usage', sa.JSON(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_query_logs_conversation_id'), 'ai_query_logs', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_ai_query_logs_user_id'), 'ai_query_logs', ['user_id'], unique=False)
    op.create_table('drafts',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('draft_type', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.Column('content', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source_question', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('related_sources', sa.JSON(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_drafts_conversation_id'), 'drafts', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_drafts_draft_type'), 'drafts', ['draft_type'], unique=False)
    op.create_index(op.f('ix_drafts_status'), 'drafts', ['status'], unique=False)
    op.create_index(op.f('ix_drafts_tenant_id'), 'drafts', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_drafts_user_id'), 'drafts', ['user_id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('content', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('meta_json', sa.JSON(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_messages_tenant_id'), 'messages', ['tenant_id'], unique=False)
    op.create_table('rag_index_jobs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('knowledge_document_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('job_type', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('started_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('finished_at', backend.app.db.models.UTCDateTime(), nullable=True),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('retry_count', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_document_id'], ['knowledge_documents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rag_index_jobs_knowledge_document_id'), 'rag_index_jobs', ['knowledge_document_id'], unique=False)
    op.create_index(op.f('ix_rag_index_jobs_status'), 'rag_index_jobs', ['status'], unique=False)
    op.create_table('graph_checkpoint_bindings',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('thread_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('graph_schema_version', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('checkpoint_schema_version', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('latest_checkpoint_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('authorization_version', sa.Integer(), nullable=False),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.run_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'thread_id')
    )
    op.create_index(op.f('ix_graph_checkpoint_bindings_run_id'), 'graph_checkpoint_bindings', ['run_id'], unique=False)
    op.create_index(op.f('ix_graph_checkpoint_bindings_tenant_id'), 'graph_checkpoint_bindings', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_graph_checkpoint_bindings_thread_id'), 'graph_checkpoint_bindings', ['thread_id'], unique=False)
    op.create_index(op.f('ix_graph_checkpoint_bindings_user_id'), 'graph_checkpoint_bindings', ['user_id'], unique=False)
    op.create_table('query_feedback',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('query_log_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('rating', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
    sa.Column('comment', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['query_log_id'], ['ai_query_logs.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('query_log_id', 'user_id')
    )
    op.create_index(op.f('ix_query_feedback_query_log_id'), 'query_feedback', ['query_log_id'], unique=False)
    op.create_index(op.f('ix_query_feedback_user_id'), 'query_feedback', ['user_id'], unique=False)
    op.create_table('run_events',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('event_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
    sa.Column('stage', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
    sa.Column('public_status', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('occurred_at', backend.app.db.models.UTCDateTime(), nullable=False),
    sa.Column('trace_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.run_id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'sequence')
    )
    op.create_index(op.f('ix_run_events_event_id'), 'run_events', ['event_id'], unique=True)
    op.create_index(op.f('ix_run_events_event_type'), 'run_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_run_events_run_id'), 'run_events', ['run_id'], unique=False)
    op.create_index(op.f('ix_run_events_tenant_id'), 'run_events', ['tenant_id'], unique=False)
    # ### end Alembic commands ###

    # Stage 2 additions that autogenerate cannot express.
    _add_check_constraints()
    _seed_legacy_tenant()
    _create_tenant_isolation_policies()


def downgrade() -> None:
    """Downgrade schema when the staged recovery policy permits it."""
    # Policies must go before their tables, and only exist on PostgreSQL.
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(TENANT_SCOPED_TABLES):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM user_role_grants WHERE tenant_id = :tenant_id "
            "AND granted_by IS NULL"
        ),
        {"tenant_id": LEGACY_TENANT_ID},
    )
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_run_events_tenant_id'), table_name='run_events')
    op.drop_index(op.f('ix_run_events_run_id'), table_name='run_events')
    op.drop_index(op.f('ix_run_events_event_type'), table_name='run_events')
    op.drop_index(op.f('ix_run_events_event_id'), table_name='run_events')
    op.drop_table('run_events')
    op.drop_index(op.f('ix_query_feedback_user_id'), table_name='query_feedback')
    op.drop_index(op.f('ix_query_feedback_query_log_id'), table_name='query_feedback')
    op.drop_table('query_feedback')
    op.drop_index(op.f('ix_graph_checkpoint_bindings_user_id'), table_name='graph_checkpoint_bindings')
    op.drop_index(op.f('ix_graph_checkpoint_bindings_thread_id'), table_name='graph_checkpoint_bindings')
    op.drop_index(op.f('ix_graph_checkpoint_bindings_tenant_id'), table_name='graph_checkpoint_bindings')
    op.drop_index(op.f('ix_graph_checkpoint_bindings_run_id'), table_name='graph_checkpoint_bindings')
    op.drop_table('graph_checkpoint_bindings')
    op.drop_index(op.f('ix_rag_index_jobs_status'), table_name='rag_index_jobs')
    op.drop_index(op.f('ix_rag_index_jobs_knowledge_document_id'), table_name='rag_index_jobs')
    op.drop_table('rag_index_jobs')
    op.drop_index(op.f('ix_messages_tenant_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_drafts_user_id'), table_name='drafts')
    op.drop_index(op.f('ix_drafts_tenant_id'), table_name='drafts')
    op.drop_index(op.f('ix_drafts_status'), table_name='drafts')
    op.drop_index(op.f('ix_drafts_draft_type'), table_name='drafts')
    op.drop_index(op.f('ix_drafts_conversation_id'), table_name='drafts')
    op.drop_table('drafts')
    op.drop_index(op.f('ix_ai_query_logs_user_id'), table_name='ai_query_logs')
    op.drop_index(op.f('ix_ai_query_logs_conversation_id'), table_name='ai_query_logs')
    op.drop_table('ai_query_logs')
    op.drop_index(op.f('ix_agent_runs_user_id'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_thread_id'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_tenant_id'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_status'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_run_id'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_kind'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_conversation_id'), table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_table('user_roles')
    op.drop_index(op.f('ix_user_role_grants_user_id'), table_name='user_role_grants')
    op.drop_index(op.f('ix_user_role_grants_tenant_id'), table_name='user_role_grants')
    op.drop_index(op.f('ix_user_role_grants_role_id'), table_name='user_role_grants')
    op.drop_table('user_role_grants')
    op.drop_index(op.f('ix_knowledge_documents_tenant_id'), table_name='knowledge_documents')
    op.drop_index(op.f('ix_knowledge_documents_knowledge_base_id'), table_name='knowledge_documents')
    op.drop_index(op.f('ix_knowledge_documents_index_status'), table_name='knowledge_documents')
    op.drop_index(op.f('ix_knowledge_documents_external_id'), table_name='knowledge_documents')
    op.drop_index(op.f('ix_knowledge_documents_content_hash'), table_name='knowledge_documents')
    op.drop_table('knowledge_documents')
    op.drop_index(op.f('ix_knowledge_base_permissions_subject_type'), table_name='knowledge_base_permissions')
    op.drop_index(op.f('ix_knowledge_base_permissions_subject_id'), table_name='knowledge_base_permissions')
    op.drop_index(op.f('ix_knowledge_base_permissions_knowledge_base_id'), table_name='knowledge_base_permissions')
    op.drop_table('knowledge_base_permissions')
    op.drop_index(op.f('ix_faq_drafts_status'), table_name='faq_drafts')
    op.drop_index(op.f('ix_faq_drafts_source_document_id'), table_name='faq_drafts')
    op.drop_index(op.f('ix_faq_drafts_source_conversation_id'), table_name='faq_drafts')
    op.drop_index(op.f('ix_faq_drafts_knowledge_base_id'), table_name='faq_drafts')
    op.drop_table('faq_drafts')
    op.drop_index(op.f('ix_eval_results_retrieval_eval_item_id'), table_name='eval_results')
    op.drop_index(op.f('ix_eval_results_eval_run_id'), table_name='eval_results')
    op.drop_index(op.f('ix_eval_results_eval_case_id'), table_name='eval_results')
    op.drop_table('eval_results')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_tenant_id'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_status'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_tenant_id'), table_name='users')
    op.drop_index(op.f('ix_users_status'), table_name='users')
    op.drop_index(op.f('ix_users_external_subject'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_index(op.f('ix_users_department_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_roles_tenant_id'), table_name='roles')
    op.drop_index(op.f('ix_roles_code'), table_name='roles')
    op.drop_table('roles')
    op.drop_index(op.f('ix_memory_items_tenant_id'), table_name='memory_items')
    op.drop_index(op.f('ix_memory_items_owner_type'), table_name='memory_items')
    op.drop_index(op.f('ix_memory_items_owner_id'), table_name='memory_items')
    op.drop_index(op.f('ix_memory_items_memory_type'), table_name='memory_items')
    op.drop_table('memory_items')
    op.drop_index(op.f('ix_knowledge_bases_tenant_id'), table_name='knowledge_bases')
    op.drop_index(op.f('ix_knowledge_bases_status'), table_name='knowledge_bases')
    op.drop_index(op.f('ix_knowledge_bases_department_id'), table_name='knowledge_bases')
    op.drop_index(op.f('ix_knowledge_bases_code'), table_name='knowledge_bases')
    op.drop_table('knowledge_bases')
    op.drop_index(op.f('ix_idempotency_records_tenant_id'), table_name='idempotency_records')
    op.drop_index(op.f('ix_idempotency_records_state'), table_name='idempotency_records')
    op.drop_index(op.f('ix_idempotency_records_operation'), table_name='idempotency_records')
    op.drop_index(op.f('ix_idempotency_records_idempotency_key'), table_name='idempotency_records')
    op.drop_table('idempotency_records')
    op.drop_index(op.f('ix_eval_runs_tenant_id'), table_name='eval_runs')
    op.drop_index(op.f('ix_eval_runs_status'), table_name='eval_runs')
    op.drop_index(op.f('ix_eval_runs_request_id'), table_name='eval_runs')
    op.drop_table('eval_runs')
    op.drop_index(op.f('ix_audit_events_trace_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_tenant_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_run_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_request_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_outcome'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_event_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_action'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_tools_name'), table_name='tools')
    op.drop_table('tools')
    op.drop_index(op.f('ix_tool_call_logs_user_id'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_tool_name'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_status'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_request_id'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_conversation_id'), table_name='tool_call_logs')
    op.drop_table('tool_call_logs')
    op.drop_index(op.f('ix_tenants_status'), table_name='tenants')
    op.drop_index(op.f('ix_tenants_code'), table_name='tenants')
    op.drop_table('tenants')
    op.drop_index(op.f('ix_skills_name'), table_name='skills')
    op.drop_table('skills')
    op.drop_index(op.f('ix_retrieval_eval_items_eval_case_id'), table_name='retrieval_eval_items')
    op.drop_table('retrieval_eval_items')
    op.drop_index(op.f('ix_model_providers_name'), table_name='model_providers')
    op.drop_index(op.f('ix_model_providers_capability'), table_name='model_providers')
    op.drop_table('model_providers')
    op.drop_index(op.f('ix_mcp_servers_server_type'), table_name='mcp_servers')
    op.drop_index(op.f('ix_mcp_servers_name'), table_name='mcp_servers')
    op.drop_index(op.f('ix_mcp_servers_integration_mode'), table_name='mcp_servers')
    op.drop_index(op.f('ix_mcp_servers_health_status'), table_name='mcp_servers')
    op.drop_table('mcp_servers')
    op.drop_index(op.f('ix_eval_cases_category'), table_name='eval_cases')
    op.drop_table('eval_cases')
    op.drop_index(op.f('ix_departments_code'), table_name='departments')
    op.drop_table('departments')
    op.drop_index(op.f('ix_audit_logs_target_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_request_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_actor_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    # ### end Alembic commands ###
