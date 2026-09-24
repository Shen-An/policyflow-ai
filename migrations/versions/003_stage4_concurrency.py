"""stage 4 concurrency and durability

Migration phase: expand
Revision ID: 003
Revises: 002
Create Date: 2026-09-24

Stage 4 expand migration. It is purely additive: it creates the six durable
tables that back User Story 2 ("stable use under large-scale concurrency") and
nothing else touches the existing schema.

* ``durable_jobs`` -- the PostgreSQL-authoritative unit of background work;
* ``outbox_events`` -- the transactional outbox the publisher drains to RabbitMQ;
* ``quota_policies`` -- versioned admission limits (a global policy has a null
  ``tenant_id``);
* ``quota_leases`` -- the durable audit correlate of a Redis concurrency lease;
* ``usage_records`` -- append-only reserved/actual usage;
* ``capacity_test_runs`` -- immutable capacity-claim evidence, not tenant-scoped.

Unlike the Stage-2 chain, the enforce step (002) already ran *before* this
revision, so it never saw these tables and could neither force their RLS nor
grant the application role on them. This revision therefore self-applies the
same guarantees it needs: it ENABLEs and FORCEs row-level security and creates
the ``tenant_isolation`` policy on the five tenant-scoped tables (the migration
role owns the tables, so an unforced policy would be silently bypassed), and it
grants the restricted ``policyflow_app`` role the DML it needs on the new
tables. ``quota_policies`` keeps its ``tenant_id`` nullable on purpose -- a
global policy is shared -- so its policy is null-tolerant. ``capacity_test_runs``
carries no ``tenant_id`` and gets no RLS.

Generate revisions with an explicit phase, for example:
    alembic -x phase=expand revision -m "stage 4 durable tables"
Run a constrained upgrade with:
    alembic -x phase=expand upgrade head
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

import backend.app.db.models
from migrations.phases import validate_migration_phase

revision: str = '003'
down_revision: str | Sequence[str] | None = '002'
branch_labels: str | Sequence[str] | None = ('phase:expand:003',)
depends_on: str | Sequence[str] | None = None
migration_phase = validate_migration_phase(
    'expand', revision
)

#: The application role the service connects as; it must never bypass RLS, so
#: this migration re-grants it on the new tables rather than relying on 002's
#: schema-wide grant, which ran before these tables existed.
APPLICATION_ROLE = "policyflow_app"

#: Stage-4 tables that carry ``tenant_id`` and must be RLS-isolated. All but
#: ``quota_policies`` require a present owner; ``quota_policies`` tolerates a
#: null owner because a global policy is shared across tenants.
STRICT_TENANT_TABLES: tuple[str, ...] = (
    "durable_jobs",
    "outbox_events",
    "quota_leases",
    "usage_records",
)

#: All six tables 003 creates, in creation order (drop is the reverse).
NEW_TABLES: tuple[str, ...] = (
    "durable_jobs",
    "outbox_events",
    "quota_policies",
    "quota_leases",
    "usage_records",
    "capacity_test_runs",
)


def _dialect_name() -> str:
    """Return the active dialect name, known both online and offline."""
    return str(op.get_context().dialect.name)


def _create_durable_jobs() -> None:
    op.create_table(
        'durable_jobs',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('idempotency_key', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('payload_schema_version', sa.Integer(), nullable=False),
        sa.Column('payload_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('priority_lane', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column('lease_owner', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('lease_expires_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('heartbeat_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('deadline_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('result_ref', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column('last_error_code', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'kind', 'idempotency_key', name='uq_durable_jobs_idem'),
    )
    op.create_index(op.f('ix_durable_jobs_tenant_id'), 'durable_jobs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_durable_jobs_run_id'), 'durable_jobs', ['run_id'], unique=False)
    op.create_index(op.f('ix_durable_jobs_kind'), 'durable_jobs', ['kind'], unique=False)
    op.create_index(
        op.f('ix_durable_jobs_idempotency_key'), 'durable_jobs', ['idempotency_key'], unique=False
    )
    op.create_index(op.f('ix_durable_jobs_state'), 'durable_jobs', ['state'], unique=False)
    op.create_index(
        op.f('ix_durable_jobs_priority_lane'), 'durable_jobs', ['priority_lane'], unique=False
    )
    op.create_index(
        op.f('ix_durable_jobs_available_at'), 'durable_jobs', ['available_at'], unique=False
    )


def _create_outbox_events() -> None:
    op.create_table(
        'outbox_events',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('aggregate_type', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('aggregate_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('aggregate_version', sa.Integer(), nullable=False),
        sa.Column('event_type', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('delivery_state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('delivered_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('last_error_code', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'aggregate_type', 'aggregate_id', 'aggregate_version', 'event_type',
            name='uq_outbox_aggregate_version_event',
        ),
    )
    op.create_index(
        op.f('ix_outbox_events_tenant_id'), 'outbox_events', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_outbox_events_aggregate_type'), 'outbox_events', ['aggregate_type'], unique=False
    )
    op.create_index(
        op.f('ix_outbox_events_aggregate_id'), 'outbox_events', ['aggregate_id'], unique=False
    )
    op.create_index(
        op.f('ix_outbox_events_event_type'), 'outbox_events', ['event_type'], unique=False
    )
    op.create_index(
        op.f('ix_outbox_events_delivery_state'), 'outbox_events', ['delivery_state'], unique=False
    )
    op.create_index(
        op.f('ix_outbox_events_available_at'), 'outbox_events', ['available_at'], unique=False
    )


def _create_quota_policies() -> None:
    op.create_table(
        'quota_policies',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        # Deliberately nullable: a global policy has no tenant. The RLS policy
        # on this table is null-tolerant so global rows are shared.
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('workload', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=True),
        sa.Column('requests_per_window', sa.Integer(), nullable=False),
        sa.Column('window_seconds', sa.Integer(), nullable=False),
        sa.Column('tokens_per_window', sa.Integer(), nullable=False),
        sa.Column('max_concurrency', sa.Integer(), nullable=False),
        sa.Column('queue_admission_limit', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'scope', 'tenant_id', 'workload', 'version', name='uq_quota_policy_version'
        ),
    )
    op.create_index(op.f('ix_quota_policies_scope'), 'quota_policies', ['scope'], unique=False)
    op.create_index(
        op.f('ix_quota_policies_tenant_id'), 'quota_policies', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_quota_policies_workload'), 'quota_policies', ['workload'], unique=False
    )
    op.create_index(op.f('ix_quota_policies_active'), 'quota_policies', ['active'], unique=False)


def _create_quota_leases() -> None:
    op.create_table(
        'quota_leases',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('owner', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('resource', sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column('acquired_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('expires_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('released_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('outcome', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_quota_leases_tenant_id'), 'quota_leases', ['tenant_id'], unique=False
    )
    op.create_index(op.f('ix_quota_leases_run_id'), 'quota_leases', ['run_id'], unique=False)
    op.create_index(op.f('ix_quota_leases_owner'), 'quota_leases', ['owner'], unique=False)
    op.create_index(op.f('ix_quota_leases_resource'), 'quota_leases', ['resource'], unique=False)
    op.create_index(op.f('ix_quota_leases_outcome'), 'quota_leases', ['outcome'], unique=False)


def _create_usage_records() -> None:
    op.create_table(
        'usage_records',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=True),
        sa.Column('model', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=True),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('reserved_tokens', sa.Integer(), nullable=False),
        sa.Column('actual_tokens', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('occurred_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_usage_records_tenant_id'), 'usage_records', ['tenant_id'], unique=False
    )
    op.create_index(op.f('ix_usage_records_run_id'), 'usage_records', ['run_id'], unique=False)
    op.create_index(op.f('ix_usage_records_user_id'), 'usage_records', ['user_id'], unique=False)
    op.create_index(op.f('ix_usage_records_kind'), 'usage_records', ['kind'], unique=False)
    op.create_index(
        op.f('ix_usage_records_occurred_at'), 'usage_records', ['occurred_at'], unique=False
    )


def _create_capacity_test_runs() -> None:
    op.create_table(
        'capacity_test_runs',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('commit_sha', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column('scenario', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('suite_version', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column('environment_manifest', sa.JSON(), nullable=False),
        sa.Column('topology', sa.JSON(), nullable=False),
        sa.Column('hardware', sa.JSON(), nullable=False),
        sa.Column('dataset_manifest', sa.JSON(), nullable=False),
        sa.Column('llm_mode', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column('target_profile', sa.JSON(), nullable=False),
        sa.Column('started_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=False),
        sa.Column('raw_artifact_uri', sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False),
        sa.Column(
            'raw_artifact_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column('summary_metrics', sa.JSON(), nullable=False),
        sa.Column('verdict', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('known_limits', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('raw_artifact_sha256', name='uq_capacity_raw_artifact'),
    )
    op.create_index(
        op.f('ix_capacity_test_runs_commit_sha'), 'capacity_test_runs', ['commit_sha'], unique=False
    )
    op.create_index(
        op.f('ix_capacity_test_runs_scenario'), 'capacity_test_runs', ['scenario'], unique=False
    )
    op.create_index(
        op.f('ix_capacity_test_runs_llm_mode'), 'capacity_test_runs', ['llm_mode'], unique=False
    )
    op.create_index(
        op.f('ix_capacity_test_runs_verdict'), 'capacity_test_runs', ['verdict'], unique=False
    )


# PLACEHOLDER_RLS


def _force_isolation(table: str, *, null_tolerant: bool) -> None:
    """ENABLE + FORCE RLS on ``table`` and (idempotently) create its policy.

    FORCE is issued as well as ENABLE because the migration role owns these
    tables, and PostgreSQL does not apply a policy to the owner unless the table
    is forced -- an enabled-but-unforced table would be silently unisolated.
    When ``null_tolerant`` the policy also admits a null ``tenant_id`` (a shared
    global row), which is why ``quota_policies`` can keep its owner nullable.
    """
    predicate = "tenant_id::text = NULLIF(current_setting('policyflow.tenant_id', true), '')"
    if null_tolerant:
        predicate = f"tenant_id IS NULL OR {predicate}"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public' AND tablename = '{table}'
                  AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON {table}
                USING ({predicate})
                WITH CHECK ({predicate});
            END IF;
        END
        $$;
        """
    )


def _apply_row_level_security() -> None:
    """Isolate the five tenant-scoped Stage-4 tables. ``capacity_test_runs`` is
    global (no ``tenant_id``) and is deliberately left unprotected."""
    for table in STRICT_TENANT_TABLES:
        _force_isolation(table, null_tolerant=False)
    _force_isolation("quota_policies", null_tolerant=True)


def _grant_application_role() -> None:
    """Grant the restricted service role DML on the new tables.

    002's schema-wide grant ran before these tables existed, so the role would
    otherwise have no access to them. Guarded by the role's existence so the
    migration does not fail on a database whose role provisioning differs; the
    normal chain (002 before 003) always creates it.
    """
    grants = "\n".join(
        f"            GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} "
        f"TO {APPLICATION_ROLE};"
        for table in NEW_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APPLICATION_ROLE}') THEN
{grants}
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    """Create the Stage-4 tables, then isolate and grant them on PostgreSQL."""
    _create_durable_jobs()
    _create_outbox_events()
    _create_quota_policies()
    _create_quota_leases()
    _create_usage_records()
    _create_capacity_test_runs()
    if _dialect_name() != "postgresql":
        # Table DDL is portable; RLS and role grants are PostgreSQL concerns.
        # SQLite is development/isolated-test only and carries no tenant state.
        return
    _apply_row_level_security()
    _grant_application_role()


def downgrade() -> None:
    """Drop the Stage-4 tables (and their policies) in reverse creation order."""
    if _dialect_name() == "postgresql":
        for table in (*STRICT_TENANT_TABLES, "quota_policies"):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
