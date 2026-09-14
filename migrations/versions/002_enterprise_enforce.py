"""enterprise enforce

Migration phase: enforce
Revision ID: 002
Revises: 001
Create Date: 2026-09-14

Stage 2 enforce migration. It converts the additive, nullable tenant ownership
created by ``001`` plus ``backfill_legacy_tenant`` into a hard constraint:

1. it refuses to run until the backfill ledger proves every tenant-scoped table
   was reconciled and no row is left without an owner;
2. it makes ``tenant_id`` NOT NULL on every table that carries it;
3. it FORCEs row-level security so even the table owner cannot read across
   tenants, and re-asserts the ``tenant_isolation`` policy;
4. it replaces single-column business-code uniqueness with per-tenant
   uniqueness, which is what lets two tenants both define ``finance``;
5. it guarantees a restricted application role exists that neither owns the
   tables nor holds ``BYPASSRLS``, because a superuser or table owner bypasses
   RLS entirely and would make point 3 decorative.

Generate revisions with an explicit phase, for example:
    alembic -x phase=enforce revision -m "enforce tenant ownership"
Run a constrained upgrade with:
    alembic -x phase=enforce upgrade head
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.backfill_legacy_tenant import (
    LEDGER_TABLE,
    STATUS_COMPLETED,
    TABLE_NAMES,
)
from migrations.phases import validate_migration_phase

revision: str = '002'
down_revision: str | Sequence[str] | None = '001'
branch_labels: str | Sequence[str] | None = ('phase:enforce:002',)
depends_on: str | Sequence[str] | None = None
migration_phase = validate_migration_phase(
    'enforce', revision
)

#: Application role the service must connect as. It is deliberately NOT the
#: migration role: the migration role owns the tables (and is often a
#: superuser), and either property silently defeats row-level security.
APPLICATION_ROLE = "policyflow_app"

#: Business identifiers that must be unique **per tenant** rather than globally.
#: The expand phase inherited global uniqueness from the pre-tenant schema, but a
#: second tenant must be able to reuse a username, an email or a business code,
#: and event ids are only meaningful inside the tenant that recorded them.
#:
#: Only tenant-scoped tables may appear here. ``departments``, ``skills``,
#: ``tools``, ``mcp_servers`` and ``model_providers`` still use global names and
#: are deliberately excluded until they gain tenant ownership, because a
#: composite uniqueness constraint on a table without a ``tenant_id`` column
#: cannot exist.
PER_TENANT_UNIQUE_CODES: tuple[tuple[str, str], ...] = (
    ("knowledge_bases", "code"),
    ("users", "username"),
    ("users", "email"),
    ("run_events", "event_id"),
    ("audit_events", "event_id"),
)

#: Every table that carries ``tenant_id`` after the backfill has run. Kept
#: explicit rather than discovered so that offline SQL generation (which has no
#: database to inspect) renders the same statements as an online upgrade. The
#: online path asserts this list matches the real schema, so drift fails loudly
#: instead of silently leaving a table unprotected.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "agent_runs",
    "ai_query_logs",
    "audit_events",
    "conversations",
    "drafts",
    "eval_cases",
    "eval_results",
    "eval_runs",
    "graph_checkpoint_bindings",
    "idempotency_records",
    "knowledge_bases",
    "knowledge_documents",
    "memory_items",
    "messages",
    "retrieval_eval_items",
    "roles",
    "run_events",
    "user_role_grants",
    "users",
)


def _offline() -> bool:
    """Return True while rendering SQL without a database connection."""
    return bool(op.get_context().as_sql)


def _dialect_name() -> str:
    """Return the active dialect name, which is known online and offline."""
    return str(op.get_context().dialect.name)


def _scoped_tables() -> tuple[str, ...]:
    """Tenant-scoped tables: discovered online, declared when rendering SQL."""
    if _offline():
        return TENANT_SCOPED_TABLES
    return _tables_with_tenant_column()


def _tables_with_tenant_column() -> tuple[str, ...]:
    """Return every public table carrying a ``tenant_id`` column.

    Discovered at runtime so an unexpected table cannot silently escape NOT NULL
    and RLS. The result is asserted to equal ``TENANT_SCOPED_TABLES`` so the
    static list used for offline rendering cannot drift from reality.
    """
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'tenant_id'
            ORDER BY table_name
            """
        )
    ).scalars()
    discovered = tuple(str(name) for name in rows)
    declared = set(TENANT_SCOPED_TABLES)
    unexpected = sorted(set(discovered) - declared)
    if unexpected:
        raise RuntimeError(
            "enforce refused: these tables carry tenant_id but are not in "
            f"TENANT_SCOPED_TABLES: {', '.join(unexpected)}; add them so they get "
            "NOT NULL and row-level security"
        )
    return discovered


def _assert_backfill_reconciled() -> None:
    """Abort unless the backfill ledger proves every table completed.

    Enforcing NOT NULL on an unreconciled table would either fail obscurely or,
    worse, strand rows that no tenant can ever read.
    """
    bind = op.get_bind()
    ledger_exists = bind.execute(
        sa.text("SELECT to_regclass(:name)"), {"name": f"public.{LEDGER_TABLE}"}
    ).scalar()
    if ledger_exists is None:
        raise RuntimeError(
            f"enforce refused: the backfill ledger {LEDGER_TABLE!r} does not exist; "
            "run `python -m migrations.backfill_legacy_tenant` before enforcing"
        )
    states = dict(
        bind.execute(
            sa.text(
                f"SELECT table_name, status FROM {LEDGER_TABLE}"  # noqa: S608
            )
        ).all()
    )
    missing = [name for name in TABLE_NAMES if name not in states]
    incomplete = [
        f"{name}={states[name]}"
        for name in TABLE_NAMES
        if name in states and states[name] != STATUS_COMPLETED
    ]
    if missing or incomplete:
        raise RuntimeError(
            "enforce refused: the tenant backfill is not reconciled "
            f"(missing={missing or 'none'}, incomplete={incomplete or 'none'}); "
            "re-run `python -m migrations.backfill_legacy_tenant`"
        )


def _assert_no_unowned_rows() -> dict[str, int]:
    """Abort when any tenant-scoped table still holds a row without an owner."""
    bind = op.get_bind()
    unowned: dict[str, int] = {}
    for table in _scoped_tables():
        count = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")  # noqa: S608
        ).scalar()
        if count:
            unowned[table] = int(count)
    if unowned:
        detail = ", ".join(f"{name}={count}" for name, count in sorted(unowned.items()))
        raise RuntimeError(
            "enforce refused: rows without tenant ownership remain "
            f"({detail}); run the backfill and reconcile before enforcing"
        )
    return unowned


def _set_tenant_not_null() -> None:
    for table in _scoped_tables():
        op.alter_column(table, "tenant_id", existing_type=sa.String(length=36), nullable=False)


def _force_tenant_isolation() -> None:
    """Enable and force RLS, then guarantee the policy exists.

    ``FORCE ROW LEVEL SECURITY`` alone is a trap: PostgreSQL's FORCE only makes
    the policy apply to the table *owner*, it does **not** switch row security
    on. The expand migration enables RLS only for the tables it gave a
    ``tenant_id`` to, so the tables whose ``tenant_id`` is added later by the
    backfill would end up with a policy that is never consulted — a silent
    no-isolation state on exactly the tables holding evaluation data and query
    logs. Both statements are therefore issued, and both are idempotent.
    """
    for table in _scoped_tables():
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
                    USING (
                        tenant_id::text
                        = NULLIF(current_setting('policyflow.tenant_id', true), '')
                    )
                    WITH CHECK (
                        tenant_id::text
                        = NULLIF(current_setting('policyflow.tenant_id', true), '')
                    );
                END IF;
            END
            $$;
            """
        )


#: Single-column uniqueness on a business code can be expressed either as a
#: unique constraint or as a bare unique index; the expand migration produced
#: the latter for ``knowledge_bases.code``. Both must be removed, otherwise the
#: per-tenant constraint is added while the global rule silently keeps applying.
_SINGLE_COLUMN_UNIQUE_CONSTRAINTS_SQL = """
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = rel.relnamespace
    WHERE ns.nspname = 'public'
      AND rel.relname = :table
      AND con.contype = 'u'
      AND (
          -- Aggregated as text so it compares against a text[] literal;
          -- ``ARRAY[:column]`` is not a valid array constructor and the bind
          -- parameter needs an explicit cast.
          SELECT array_agg(att.attname::text ORDER BY att.attname)
          FROM unnest(con.conkey) AS key(attnum)
          JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = key.attnum
      ) = ARRAY[CAST(:column AS text)]
"""

_SINGLE_COLUMN_UNIQUE_INDEXES_SQL = """
    SELECT idx.relname
    FROM pg_index i
    JOIN pg_class idx ON idx.oid = i.indexrelid
    JOIN pg_class rel ON rel.oid = i.indrelid
    JOIN pg_namespace ns ON ns.oid = rel.relnamespace
    WHERE ns.nspname = 'public'
      AND rel.relname = :table
      AND i.indisunique
      AND NOT i.indisprimary
      -- A constraint's own index is dropped with the constraint, so excluding
      -- it here keeps the two steps from fighting over the same object.
      AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)
      AND (
          SELECT array_agg(att.attname::text ORDER BY att.attname)
          FROM unnest(i.indkey) AS k(attnum)
          JOIN pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = k.attnum
      ) = ARRAY[CAST(:column AS text)]
"""


def _drop_single_column_unique(table: str, column: str) -> list[str]:
    """Drop every single-column unique rule on ``column`` and return their names.

    Both unique constraints and bare unique indexes are removed: a leftover index
    would keep enforcing global uniqueness while the migration reports success.

    Online the real names are looked up, so an unexpected name is still dropped.
    Offline there is nothing to inspect, so the conventional names the expand
    migration produced are dropped with ``IF EXISTS``; that keeps the rendered
    SQL valid whether or not each object is present.
    """
    if _offline():
        conventional = (f"{table}_{column}_key", f"ix_{table}_{column}")
        for name in conventional:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
            op.execute(f"DROP INDEX IF EXISTS {name}")
        return list(conventional)
    bind = op.get_bind()
    dropped: list[str] = []
    for name in bind.execute(
        sa.text(_SINGLE_COLUMN_UNIQUE_CONSTRAINTS_SQL), {"table": table, "column": column}
    ).scalars():
        op.drop_constraint(str(name), table, type_="unique")
        dropped.append(str(name))
    for name in bind.execute(
        sa.text(_SINGLE_COLUMN_UNIQUE_INDEXES_SQL), {"table": table, "column": column}
    ).scalars():
        op.execute(f'DROP INDEX IF EXISTS "{name}"')
        dropped.append(str(name))
    return dropped


def _add_per_tenant_uniqueness() -> None:
    """Replace global business-code uniqueness with per-tenant uniqueness."""
    for table, column in PER_TENANT_UNIQUE_CODES:
        _drop_single_column_unique(table, column)
        op.create_unique_constraint(
            f"uq_{table}_tenant_{column}", table, ["tenant_id", column]
        )


def _ensure_application_role() -> None:
    """Guarantee a restricted role exists for the application to connect as.

    A table owner bypasses RLS unless FORCE is set, and a ``BYPASSRLS`` role (or
    a superuser) bypasses it regardless. The migration may legitimately run as
    an owner, so the guarantee is expressed on the role the *service* uses.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APPLICATION_ROLE}') THEN
                -- LOGIN so the service can connect as this role, but with no
                -- password: credential injection is an operator concern and
                -- must never be baked into a migration.
                CREATE ROLE {APPLICATION_ROLE} LOGIN NOSUPERUSER
                    NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    # A session may switch into this role to verify RLS, and the running service
    # may connect as it directly; both need the schema and DML privileges.
    op.execute(f"GRANT {APPLICATION_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APPLICATION_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {APPLICATION_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APPLICATION_ROLE}")
    if not _offline():
        _assert_application_role_restricted()


def _assert_application_role_restricted() -> None:
    """Refuse the upgrade if the application role could bypass RLS."""
    bind = op.get_bind()
    bypass = bind.execute(
        sa.text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = :role"),
        {"role": APPLICATION_ROLE},
    ).scalar()
    if bypass:
        raise RuntimeError(
            f"enforce refused: role {APPLICATION_ROLE!r} has BYPASSRLS or is a superuser, "
            "so row-level security would not constrain the application"
        )
    owned = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM pg_class rel
            JOIN pg_roles rol ON rol.oid = rel.relowner
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = 'public' AND rol.rolname = :role AND rel.relkind = 'r'
            """
        ),
        {"role": APPLICATION_ROLE},
    ).scalar()
    if owned:
        raise RuntimeError(
            f"enforce refused: role {APPLICATION_ROLE!r} owns {owned} application table(s); "
            "an owner can bypass row-level security, so the application must not own them"
        )


def upgrade() -> None:
    """Upgrade schema within the declared migration phase."""
    if _dialect_name() != "postgresql":
        # Tenant ownership enforcement, RLS and role guarantees are PostgreSQL
        # concerns; SQLite never carries production business state.
        return
    if not _offline():
        # Data guards need a live connection. Rendering SQL offline cannot check
        # them, which is why the guarded run is the only supported execution
        # path: `alembic upgrade --sql` output must be reviewed, not trusted.
        _assert_backfill_reconciled()
        _assert_no_unowned_rows()
    _set_tenant_not_null()
    _force_tenant_isolation()
    _add_per_tenant_uniqueness()
    _ensure_application_role()


def downgrade() -> None:
    """Relax the enforced constraints without reversing any data change.

    Destructive reversal is intentionally NOT automated. Re-creating nullable
    ``tenant_id`` columns is safe, but restoring global uniqueness is only
    possible while no two tenants actually share an identifier, so it is done
    with the same names the expand migration used and the application role is
    left in place because other objects may depend on it. No ``tenant_id`` value
    is ever cleared.
    """
    if _dialect_name() != "postgresql":
        return
    for table, column in PER_TENANT_UNIQUE_CODES:
        op.drop_constraint(f"uq_{table}_tenant_{column}", table, type_="unique")
        # The expand migration expressed these rules as unique indexes named
        # ``ix_<table>_<column>``, so the reversal restores that exact shape.
        op.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "ix_{table}_{column}" ON {table} ("{column}")'
        )
    for table in _scoped_tables():
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.alter_column(table, "tenant_id", existing_type=sa.String(length=36), nullable=True)
