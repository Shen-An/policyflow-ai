"""stage 6 workspaces, change sets, approvals and submissions

Migration phase: expand
Revision ID: 005
Revises: 004
Create Date: 2026-10-05

Stage 6 expand migration. Purely additive: it creates the six tables that back
User Story 1B ("securely edit and submit enterprise materials") and touches
nothing that already exists.

* ``task_workspaces`` -- the short-lived editing context bound to one run;
* ``workspace_inputs`` -- the explicitly selected material versions (the only way
  a version enters a workspace);
* ``change_sets`` -- a proposed set of edits with the digests an approval binds to;
* ``change_set_items`` -- one edit per path, path unique within the workspace;
* ``approval_requests`` -- one immutable human-review target per change set;
* ``submission_jobs`` -- the unique external-submission record carrying the
  ``(tenant_id, connector_id, idempotency_key)`` at-most-once key.

Creation order is load-bearing: ``task_workspaces`` before ``workspace_inputs``
and ``change_sets`` (which reference it), ``change_sets`` before
``change_set_items`` and ``approval_requests``, and ``approval_requests`` before
``submission_jobs``.

Like 003/004, this revision runs after the enforce step (002), so it self-applies
forced row-level security and the ``policyflow_app`` grant on the new tables.

Generate revisions with an explicit phase, for example:
    alembic -x phase=expand revision -m "stage 6 workspace tables"
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

import backend.app.db.models
from backend.app.db.models import (
    APPROVAL_STATUSES,
    CHANGE_ITEM_OPERATIONS,
    CHANGE_SET_SIDE_EFFECT_CLASSES,
    CHANGE_SET_STATES,
    SUBMISSION_STATES,
    WORKSPACE_INPUT_PURPOSES,
    WORKSPACE_STATUSES,
)
from migrations.phases import validate_migration_phase

revision: str = '005'
down_revision: str | Sequence[str] | None = '004'
branch_labels: str | Sequence[str] | None = ('phase:expand:005',)
depends_on: str | Sequence[str] | None = None
migration_phase = validate_migration_phase('expand', revision)

APPLICATION_ROLE = "policyflow_app"

NEW_TABLES: tuple[str, ...] = (
    "task_workspaces",
    "workspace_inputs",
    "change_sets",
    "change_set_items",
    "approval_requests",
    "submission_jobs",
)

#: Every Stage-6 table is tenant-owned; all are RLS-isolated with a strict
#: (non-null-tolerant) predicate. There is no shared/global Stage-6 row.
STRICT_TENANT_TABLES: tuple[str, ...] = NEW_TABLES

CHECK_CONSTRAINTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("task_workspaces", "ck_task_workspaces_status", "status", tuple(sorted(WORKSPACE_STATUSES))),
    (
        "workspace_inputs",
        "ck_workspace_inputs_purpose",
        "purpose",
        tuple(sorted(WORKSPACE_INPUT_PURPOSES)),
    ),
    ("change_sets", "ck_change_sets_state", "state", tuple(sorted(CHANGE_SET_STATES))),
    (
        "change_sets",
        "ck_change_sets_side_effect_class",
        "side_effect_class",
        tuple(sorted(CHANGE_SET_SIDE_EFFECT_CLASSES)),
    ),
    (
        "change_set_items",
        "ck_change_set_items_operation",
        "operation",
        tuple(sorted(CHANGE_ITEM_OPERATIONS)),
    ),
    (
        "approval_requests",
        "ck_approval_requests_status",
        "status",
        tuple(sorted(APPROVAL_STATUSES)),
    ),
    ("submission_jobs", "ck_submission_jobs_state", "state", tuple(sorted(SUBMISSION_STATES))),
)

#: Row-level rule: a change-set item's create/update/delete must agree with
#: whether it names a source version (see ``change_item_source_error``).
ROW_CHECK_CONSTRAINTS: dict[str, tuple[str, str]] = {
    "ck_change_set_items_source_chain": (
        "change_set_items",
        "(operation = 'create' AND source_version_id IS NULL) OR "
        "(operation IN ('update', 'delete') AND source_version_id IS NOT NULL)",
    ),
}


def _dialect_name() -> str:
    return str(op.get_context().dialect.name)


def _create_task_workspaces() -> None:
    op.create_table(
        'task_workspaces',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('session_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('sandbox_job_ref', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column(
            'input_manifest_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column('policy_snapshot', sa.JSON(), nullable=False),
        sa.Column('expires_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('closed_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('tenant_id', 'run_id', 'user_id', 'status', 'expires_at'):
        op.create_index(
            op.f(f'ix_task_workspaces_{column}'), 'task_workspaces', [column], unique=False
        )


def _create_workspace_inputs() -> None:
    op.create_table(
        'workspace_inputs',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('workspace_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'material_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False
        ),
        sa.Column('purpose', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('staged_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('read_only', sa.Boolean(), nullable=False),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['material_version_id'], ['material_versions.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['task_workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'workspace_id', 'material_version_id', name='uq_workspace_inputs_version'
        ),
    )
    for column in ('tenant_id', 'workspace_id', 'material_version_id'):
        op.create_index(
            op.f(f'ix_workspace_inputs_{column}'), 'workspace_inputs', [column], unique=False
        )


def _create_change_sets() -> None:
    op.create_table(
        'change_sets',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('workspace_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'source_manifest_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column(
            'evidence_set_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
        sa.Column(
            'side_effect_class', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False
        ),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['task_workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('tenant_id', 'workspace_id', 'run_id', 'side_effect_class', 'state'):
        op.create_index(
            op.f(f'ix_change_sets_{column}'), 'change_sets', [column], unique=False
        )


def _create_change_set_items() -> None:
    op.create_table(
        'change_set_items',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('change_set_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('workspace_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'source_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column(
            'proposed_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('operation', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('normalized_path', sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=False),
        sa.Column('before_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('after_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('diff_artifact_ref', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['change_set_id'], ['change_sets.id'], ),
        sa.ForeignKeyConstraint(['proposed_version_id'], ['material_versions.id'], ),
        sa.ForeignKeyConstraint(['source_version_id'], ['material_versions.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['task_workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'normalized_path', name='uq_change_set_items_path'),
    )
    for column in ('tenant_id', 'change_set_id', 'workspace_id', 'normalized_path'):
        op.create_index(
            op.f(f'ix_change_set_items_{column}'), 'change_set_items', [column], unique=False
        )


def _create_approval_requests() -> None:
    op.create_table(
        'approval_requests',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('change_set_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('destination', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column('action_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('requested_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('decided_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('authorization_version', sa.Integer(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('expires_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('decided_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['change_set_id'], ['change_sets.id'], ),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('tenant_id', 'run_id', 'change_set_id', 'action_digest', 'status', 'expires_at'):
        op.create_index(
            op.f(f'ix_approval_requests_{column}'), 'approval_requests', [column], unique=False
        )


def _create_submission_jobs() -> None:
    op.create_table(
        'submission_jobs',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('approval_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('connector_id', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('destination', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column('idempotency_key', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column(
            'expected_target_version', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column(
            'provider_receipt', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True
        ),
        sa.Column('sanitized_result', sa.JSON(), nullable=False),
        sa.Column('next_attempt_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('last_error_code', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['approval_id'], ['approval_requests.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'connector_id', 'idempotency_key', name='uq_submission_jobs_idem'
        ),
    )
    for column in (
        'tenant_id',
        'run_id',
        'approval_id',
        'connector_id',
        'idempotency_key',
        'state',
        'next_attempt_at',
    ):
        op.create_index(
            op.f(f'ix_submission_jobs_{column}'), 'submission_jobs', [column], unique=False
        )


def _apply_check_constraints() -> None:
    for table, name, column, allowed in CHECK_CONSTRAINTS:
        values = ", ".join(f"'{value}'" for value in allowed)
        op.create_check_constraint(name, table, f"{column} IN ({values})")
    for name, (table, predicate) in ROW_CHECK_CONSTRAINTS.items():
        op.create_check_constraint(name, table, predicate)


def _force_isolation(table: str) -> None:
    predicate = "tenant_id::text = NULLIF(current_setting('policyflow.tenant_id', true), '')"
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
    for table in STRICT_TENANT_TABLES:
        _force_isolation(table)


def _grant_application_role() -> None:
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
    _create_task_workspaces()
    _create_workspace_inputs()
    _create_change_sets()
    _create_change_set_items()
    _create_approval_requests()
    _create_submission_jobs()
    _apply_check_constraints()
    if _dialect_name() != "postgresql":
        return
    _apply_row_level_security()
    _grant_application_role()


def downgrade() -> None:
    if _dialect_name() == "postgresql":
        for table in STRICT_TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
