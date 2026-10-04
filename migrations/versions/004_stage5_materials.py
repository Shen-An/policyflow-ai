"""stage 5 materials, object versions and vector manifests

Migration phase: expand
Revision ID: 004
Revises: 003
Create Date: 2026-10-04

Stage 5 expand migration. Purely additive: it creates the six tables that back
User Story 1A ("securely store enterprise materials") and touches nothing that
already exists.

* ``materials`` -- the logical enterprise item and its active-version pointer;
* ``object_versions`` -- one immutable provider object version (bucket alias,
  opaque key, provider VersionId, hash, scan and deletion state);
* ``material_versions`` -- the immutable version chain;
* ``embedding_versions`` -- the retrieval contract a vector set was built under;
* ``vector_manifests`` -- what a material/document owns in Milvus, plus the
  ``retrievable`` switch a query filters on;
* ``reconciliation_issues`` -- detected cross-store disagreements.

Creation order is load-bearing, because a referenced table must already exist:
``materials`` before ``object_versions`` (which references it), and
``object_versions`` before ``material_versions`` (which points at it).
``materials.active_version_id`` and ``object_versions.material_version_id`` are
deliberately *not* foreign keys -- they would close a dependency cycle that
PostgreSQL cannot create in one pass and SQLite cannot ``ALTER TABLE ... ADD
CONSTRAINT`` afterwards. See the model docstrings: the authoritative direction is
``material_versions.object_version_id`` plus the CAS-activated manifest, and a
disagreement between the two directions is precisely what the ``version_drift`` /
``orphan_object`` reconciliation checks exist to find.

Like 003, this revision runs *after* the enforce step (002), so 002 never saw
these tables and could neither force their row-level security nor grant the
application role on them. This revision therefore self-applies both.

Two invariants are expressed as partial unique indexes rather than as
application convention, because they are the ones that silently corrupt
retrieval when violated:

* at most one ``active`` ``EmbeddingVersion`` per knowledge-base cohort (two
  would mix vector spaces);
* at most one ``retrievable`` ``VectorManifest`` per material and per document
  (two would make old and new versions simultaneously authoritative).

PostgreSQL enforces both with ``CREATE UNIQUE INDEX ... WHERE``. SQLite supports
partial indexes too, so development keeps the same guarantee.

Generate revisions with an explicit phase, for example:
    alembic -x phase=expand revision -m "stage 5 material storage"
Run a constrained upgrade with:
    alembic -x phase=expand upgrade head
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

import backend.app.db.models
from backend.app.db.models import (
    EMBEDDING_VERSION_STATUSES,
    MATERIAL_SOURCE_TYPES,
    MATERIAL_STATUSES,
    MATERIAL_VERSION_STATUSES,
    OBJECT_DELETION_STATES,
    OBJECT_SCAN_STATUSES,
    RECONCILIATION_ISSUE_KINDS,
    RECONCILIATION_SEVERITIES,
    RECONCILIATION_STATES,
    RECONCILIATION_STORE_PAIRS,
    VECTOR_DELETION_STATES,
)
from migrations.phases import validate_migration_phase

revision: str = '004'
down_revision: str | Sequence[str] | None = '003'
branch_labels: str | Sequence[str] | None = ('phase:expand:004',)
depends_on: str | Sequence[str] | None = None
migration_phase = validate_migration_phase(
    'expand', revision
)

#: The application role the service connects as; it must never bypass RLS, so
#: this migration re-grants it on the new tables (002's schema-wide grant ran
#: before these tables existed).
APPLICATION_ROLE = "policyflow_app"

#: All six tables this revision creates, in creation order (drop reverses it).
NEW_TABLES: tuple[str, ...] = (
    "materials",
    "object_versions",
    "material_versions",
    "embedding_versions",
    "vector_manifests",
    "reconciliation_issues",
)

#: Every Stage-5 table is tenant-owned, so every one is RLS-isolated with a
#: strict (non-null-tolerant) predicate. There is no shared/global Stage-5 row.
STRICT_TENANT_TABLES: tuple[str, ...] = NEW_TABLES

#: ``(table, constraint_name, column, allowed_values)``. The allowed set is read
#: from the Python vocabulary so the two can never drift: the contract test
#: compares both directions.
CHECK_CONSTRAINTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "materials",
        "ck_materials_source_type",
        "source_type",
        tuple(sorted(MATERIAL_SOURCE_TYPES)),
    ),
    ("materials", "ck_materials_status", "status", tuple(sorted(MATERIAL_STATUSES))),
    (
        "material_versions",
        "ck_material_versions_status",
        "status",
        tuple(sorted(MATERIAL_VERSION_STATUSES)),
    ),
    (
        "object_versions",
        "ck_object_versions_scan_status",
        "scan_status",
        tuple(sorted(OBJECT_SCAN_STATUSES)),
    ),
    (
        "object_versions",
        "ck_object_versions_deletion_state",
        "deletion_state",
        tuple(sorted(OBJECT_DELETION_STATES)),
    ),
    (
        "embedding_versions",
        "ck_embedding_versions_status",
        "status",
        tuple(sorted(EMBEDDING_VERSION_STATUSES)),
    ),
    (
        "vector_manifests",
        "ck_vector_manifests_deletion_state",
        "deletion_state",
        tuple(sorted(VECTOR_DELETION_STATES)),
    ),
    (
        "reconciliation_issues",
        "ck_reconciliation_issues_store_pair",
        "store_pair",
        tuple(sorted(RECONCILIATION_STORE_PAIRS)),
    ),
    (
        "reconciliation_issues",
        "ck_reconciliation_issues_issue_kind",
        "issue_kind",
        tuple(sorted(RECONCILIATION_ISSUE_KINDS)),
    ),
    (
        "reconciliation_issues",
        "ck_reconciliation_issues_severity",
        "severity",
        tuple(sorted(RECONCILIATION_SEVERITIES)),
    ),
    (
        "reconciliation_issues",
        "ck_reconciliation_issues_state",
        "state",
        tuple(sorted(RECONCILIATION_STATES)),
    ),
)

#: Row-level rules that constrain a *combination* of columns rather than one
#: column's vocabulary: ``{name: (table, sql_predicate)}``.
ROW_CHECK_CONSTRAINTS: dict[str, tuple[str, str]] = {
    # ``version_number`` starts at 1. SQLModel ``table=True`` classes skip
    # Pydantic validation, so the ``ge=1`` next to the model documents intent and
    # this is what actually rejects a bad value.
    "ck_material_versions_number_positive": ("material_versions", "version_number >= 1"),
    # Only the root version may omit its parent; a later version without one
    # would fork the chain invisibly.
    "ck_material_versions_root_chain": (
        "material_versions",
        "(version_number = 1 AND source_version_id IS NULL) OR "
        "(version_number > 1 AND source_version_id IS NOT NULL)",
    ),
    # A manifest describes a material version or a document, never both/neither.
    "ck_vector_manifests_one_subject": (
        "vector_manifests",
        "(material_version_id IS NOT NULL AND document_id IS NULL) OR "
        "(material_version_id IS NULL AND document_id IS NOT NULL)",
    ),
    # A retrievable manifest must have finished indexing every expected chunk;
    # activating a partially-indexed manifest would serve truncated evidence.
    "ck_vector_manifests_retrievable_complete": (
        "vector_manifests",
        "retrievable = false OR (indexed_count = expected_count AND expected_count > 0)",
    ),
}

#: Partial unique indexes: ``{name: {table, columns, where}}``. These express the
#: "exactly one authoritative version" invariants; a plain unique constraint
#: cannot, because superseded rows legitimately repeat the same key.
PARTIAL_UNIQUE_INDEXES: dict[str, dict[str, object]] = {
    "uq_embedding_versions_active": {
        "table": "embedding_versions",
        "columns": ("tenant_id", "knowledge_base_id", "cohort"),
        "where": "status = 'active'",
    },
    "uq_vector_manifests_active_material": {
        "table": "vector_manifests",
        "columns": ("tenant_id", "knowledge_base_id", "material_id"),
        "where": "retrievable",
    },
    "uq_vector_manifests_active_document": {
        "table": "vector_manifests",
        "columns": ("tenant_id", "knowledge_base_id", "document_id"),
        "where": "retrievable",
    },
}


def _dialect_name() -> str:
    """Return the active dialect name, known both online and offline."""
    return str(op.get_context().dialect.name)


def _create_materials() -> None:
    op.create_table(
        'materials',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'knowledge_base_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('owner_user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('source_type', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            'active_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('read_only', sa.Boolean(), nullable=False),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_materials_tenant_id'), 'materials', ['tenant_id'], unique=False)
    op.create_index(
        op.f('ix_materials_knowledge_base_id'), 'materials', ['knowledge_base_id'], unique=False
    )
    op.create_index(
        op.f('ix_materials_owner_user_id'), 'materials', ['owner_user_id'], unique=False
    )
    op.create_index(op.f('ix_materials_source_type'), 'materials', ['source_type'], unique=False)
    op.create_index(op.f('ix_materials_status'), 'materials', ['status'], unique=False)
    op.create_index(
        op.f('ix_materials_active_version_id'), 'materials', ['active_version_id'], unique=False
    )


def _create_object_versions() -> None:
    op.create_table(
        'object_versions',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('material_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'material_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('bucket_alias', sqlmodel.sql.sqltypes.AutoString(length=63), nullable=False),
        sa.Column('object_key', sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False),
        sa.Column(
            'provider_version_id', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=False
        ),
        sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('media_type', sqlmodel.sql.sqltypes.AutoString(length=180), nullable=False),
        sa.Column(
            'encryption_algorithm', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False
        ),
        sa.Column(
            'encryption_key_id', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True
        ),
        sa.Column('scan_status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('deletion_state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('retention_until', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id',
            'bucket_alias',
            'object_key',
            'provider_version_id',
            name='uq_object_versions_provider',
        ),
    )
    op.create_index(
        op.f('ix_object_versions_tenant_id'), 'object_versions', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_object_versions_material_id'), 'object_versions', ['material_id'], unique=False
    )
    op.create_index(
        op.f('ix_object_versions_material_version_id'),
        'object_versions',
        ['material_version_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_object_versions_object_key'), 'object_versions', ['object_key'], unique=False
    )
    op.create_index(
        op.f('ix_object_versions_scan_status'), 'object_versions', ['scan_status'], unique=False
    )
    op.create_index(
        op.f('ix_object_versions_deletion_state'),
        'object_versions',
        ['deletion_state'],
        unique=False,
    )


def _create_material_versions() -> None:
    op.create_table(
        'material_versions',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('material_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column(
            'source_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column(
            'object_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('media_type', sqlmodel.sql.sqltypes.AutoString(length=180), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ),
        sa.ForeignKeyConstraint(['object_version_id'], ['object_versions.id'], ),
        sa.ForeignKeyConstraint(['source_version_id'], ['material_versions.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'material_id', 'version_number', name='uq_material_versions_number'
        ),
    )
    op.create_index(
        op.f('ix_material_versions_tenant_id'), 'material_versions', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_material_versions_material_id'),
        'material_versions',
        ['material_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_material_versions_version_number'),
        'material_versions',
        ['version_number'],
        unique=False,
    )
    op.create_index(
        op.f('ix_material_versions_source_version_id'),
        'material_versions',
        ['source_version_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_material_versions_object_version_id'),
        'material_versions',
        ['object_version_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_material_versions_status'), 'material_versions', ['status'], unique=False
    )


def _create_embedding_versions() -> None:
    op.create_table(
        'embedding_versions',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'knowledge_base_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False
        ),
        sa.Column('cohort', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column(
            'model_identifier', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False
        ),
        sa.Column('dimensions', sa.Integer(), nullable=False),
        sa.Column('normalization', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column(
            'chunking_policy_version',
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_embedding_versions_tenant_id'), 'embedding_versions', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_embedding_versions_knowledge_base_id'),
        'embedding_versions',
        ['knowledge_base_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_embedding_versions_cohort'), 'embedding_versions', ['cohort'], unique=False
    )
    op.create_index(
        op.f('ix_embedding_versions_status'), 'embedding_versions', ['status'], unique=False
    )


def _create_vector_manifests() -> None:
    op.create_table(
        'vector_manifests',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column(
            'knowledge_base_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False
        ),
        sa.Column('material_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column(
            'material_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True
        ),
        sa.Column('document_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column(
            'embedding_version_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False
        ),
        sa.Column('milvus_database', sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column(
            'milvus_collection', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False
        ),
        sa.Column(
            'vector_id_prefix', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False
        ),
        sa.Column('chunk_ids', sa.JSON(), nullable=False),
        sa.Column('expected_count', sa.Integer(), nullable=False),
        sa.Column('indexed_count', sa.Integer(), nullable=False),
        sa.Column('content_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('retrievable', sa.Boolean(), nullable=False),
        sa.Column('activated_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('deletion_state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('deleted_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('created_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'], ),
        sa.ForeignKeyConstraint(['embedding_version_id'], ['embedding_versions.id'], ),
        sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], ),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ),
        sa.ForeignKeyConstraint(['material_version_id'], ['material_versions.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in (
        'tenant_id',
        'knowledge_base_id',
        'material_id',
        'material_version_id',
        'document_id',
        'embedding_version_id',
        'vector_id_prefix',
        'retrievable',
        'deletion_state',
    ):
        op.create_index(
            op.f(f'ix_vector_manifests_{column}'), 'vector_manifests', [column], unique=False
        )


def _create_reconciliation_issues() -> None:
    op.create_table(
        'reconciliation_issues',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column('store_pair', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column('issue_kind', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column('resource_kind', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column('resource_id', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=False),
        sa.Column('version_id', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=False),
        sa.Column(
            'observed_fingerprint', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True
        ),
        sa.Column(
            'expected_fingerprint', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True
        ),
        sa.Column('severity', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('last_error_code', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=True),
        sa.Column('resolution', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column('detected_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('updated_at', backend.app.db.models.UTCDateTime(), nullable=False),
        sa.Column('resolved_at', backend.app.db.models.UTCDateTime(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id',
            'store_pair',
            'issue_kind',
            'resource_kind',
            'resource_id',
            'version_id',
            name='uq_reconciliation_issues_natural',
        ),
    )
    for column in (
        'tenant_id',
        'store_pair',
        'issue_kind',
        'resource_id',
        'severity',
        'state',
        'next_attempt_at',
        'detected_at',
    ):
        op.create_index(
            op.f(f'ix_reconciliation_issues_{column}'),
            'reconciliation_issues',
            [column],
            unique=False,
        )


def _apply_check_constraints() -> None:
    """Add the vocabulary and row-level CHECK constraints.

    The vocabularies are rendered from the Python constants, so the database can
    never allow a value the ORM forbids (or the reverse) -- a class of defect
    that otherwise only shows up in production.
    """
    for table, name, column, allowed in CHECK_CONSTRAINTS:
        values = ", ".join(f"'{value}'" for value in allowed)
        op.create_check_constraint(name, table, f"{column} IN ({values})")
    for name, (table, predicate) in ROW_CHECK_CONSTRAINTS.items():
        op.create_check_constraint(name, table, predicate)


def _apply_partial_unique_indexes() -> None:
    """Create the "exactly one authoritative version" partial unique indexes."""
    for name, spec in PARTIAL_UNIQUE_INDEXES.items():
        op.create_index(
            name,
            str(spec["table"]),
            list(spec["columns"]),  # type: ignore[arg-type]
            unique=True,
            postgresql_where=sa.text(str(spec["where"])),
            sqlite_where=sa.text(str(spec["where"])),
        )


def _force_isolation(table: str) -> None:
    """ENABLE + FORCE RLS on ``table`` and (idempotently) create its policy.

    FORCE is issued as well as ENABLE because the migration role owns these
    tables and PostgreSQL does not apply a policy to the owner otherwise -- an
    enabled-but-unforced table would be silently unisolated.
    """
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
    """Isolate all six Stage-5 tables; none of them has a shared/global row."""
    for table in STRICT_TENANT_TABLES:
        _force_isolation(table)


def _grant_application_role() -> None:
    """Grant the restricted service role DML on the new tables.

    Guarded by the role's existence so the migration does not fail on a database
    whose role provisioning differs; the normal chain (002 before 004) creates it.
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
    """Create the Stage-5 tables and their invariants, then isolate and grant."""
    _create_materials()
    _create_object_versions()
    _create_material_versions()
    _create_embedding_versions()
    _create_vector_manifests()
    _create_reconciliation_issues()
    _apply_check_constraints()
    _apply_partial_unique_indexes()
    if _dialect_name() != "postgresql":
        # Table DDL, CHECKs and partial indexes are portable; RLS and role
        # grants are PostgreSQL concerns. SQLite is development/isolated-test
        # only and carries no tenant state.
        return
    _apply_row_level_security()
    _grant_application_role()


def downgrade() -> None:
    """Drop the Stage-5 tables (and their policies) in reverse creation order."""
    if _dialect_name() == "postgresql":
        for table in STRICT_TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for name, spec in PARTIAL_UNIQUE_INDEXES.items():
        op.drop_index(name, table_name=str(spec["table"]))
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
