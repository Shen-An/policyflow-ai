"""Integration tests for the staged PostgreSQL migration chain.

Each test drives Alembic against its own throwaway database, so no assertion
depends on another test having run first. The Stage 2 contract under test:

* ``expand`` (001) is additive and leaves tenant ownership nullable;
* the legacy-tenant backfill is restartable and reconciles every table;
* ``enforce`` (002) refuses to run until that reconciliation is proven, and then
  makes ownership NOT NULL, forces row-level security and narrows business-code
  uniqueness to each tenant.

Nothing here is simulated: every assertion reads the live schema or the live
behaviour of the restricted application role.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from sqlalchemy.engine import make_url

from tests.conftest import REPO_ROOT, alembic_upgrade, run_legacy_backfill, scratch_database

LEGACY_TENANT_ID = "00000000-0000-0000-0000-000000000001"
UNRELATED_TENANT_ID = "00000000-0000-0000-0000-0000000000ff"
APPLICATION_ROLE = "policyflow_app"

#: The GUC the row-level-security policy reads. Unset means "no tenant", which
#: must expose no rows rather than every row.
TENANT_SETTING = "policyflow.tenant_id"


def _dsn(url: str) -> str:
    """Return a libpq DSN for an async-style SQLAlchemy URL."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def connect(url: str) -> Iterator[psycopg.Cursor]:
    """Yield an autocommit cursor against ``url``."""
    with psycopg.connect(_dsn(url), autocommit=True) as conn, conn.cursor() as cursor:
        yield cursor


def scalar(cursor: psycopg.Cursor, sql: str, params: tuple[object, ...] = ()) -> object:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return None if row is None else row[0]


def rows(cursor: psycopg.Cursor, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
    cursor.execute(sql, params)
    return cursor.fetchall()


def run_backfill_cli(
    url: str,
    *,
    batch_size: int | None = None,
    stop_after_batches: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the backfill CLI, optionally bounded to rehearse an interrupted run."""
    argv = [sys.executable, "-m", "migrations.backfill_legacy_tenant"]
    if batch_size is not None:
        argv += ["--batch-size", str(batch_size)]
    if stop_after_batches is not None:
        argv += ["--stop-after-batches", str(stop_after_batches)]
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "DATABASE_URL": url,
            "ENVIRONMENT": "test",
            "PYTHONIOENCODING": "utf-8",
        },
    )


def seed_legacy_rows(url: str) -> None:
    """Insert pre-tenant rows that the backfill must attribute to ``legacy``.

    ``eval_cases`` and ``eval_results`` are inserted without ``tenant_id``
    because that column does not exist until the backfill adds it; the other
    tables received the nullable column from the expand migration.
    """
    with connect(url) as cursor:
        cursor.execute(
            "INSERT INTO users (id, tenant_id, username, email, password_hash, "
            "display_name, status, created_at, updated_at) VALUES "
            "('user-1', NULL, 'legacy-user', 'legacy@example.com', 'x', 'Legacy', "
            "'active', now(), now())"
        )
        cursor.execute(
            "INSERT INTO conversations (id, tenant_id, user_id, title, channel, "
            "status, created_at, updated_at) VALUES "
            "('conv-1', NULL, 'user-1', 'Legacy chat', 'web', 'active', now(), now())"
        )
        for index in range(3):
            cursor.execute(
                "INSERT INTO messages (id, tenant_id, conversation_id, role, content, "
                "meta_json, created_at) VALUES (%s, NULL, 'conv-1', 'user', %s, "
                "'{}'::json, now())",
                (f"msg-{index}", f"hello {index}"),
            )
        cursor.execute(
            "INSERT INTO eval_runs (id, tenant_id, name, status, total_cases, metrics, "
            "config_snapshot, created_at) VALUES "
            "('run-1', NULL, 'Legacy run', 'completed', 1, '{}'::json, '{}'::json, now())"
        )
        cursor.execute(
            "INSERT INTO eval_cases (id, question, category, expected_answer_keywords, "
            "expected_source_documents, expected_chunk_ids, should_answer, enabled, "
            "created_at) VALUES ('case-1', 'q', 'policy', '[]'::json, '[]'::json, "
            "'[]'::json, true, true, now())"
        )
        cursor.execute(
            "INSERT INTO retrieval_eval_items (id, eval_case_id, query, knowledge_base_ids, "
            "relevant_document_ids, relevant_chunk_ids, relevance_judgement, enabled, "
            "created_at) VALUES ('item-1', 'case-1', 'q', '[]'::json, '[]'::json, "
            "'[]'::json, '{}'::json, true, now())"
        )
        cursor.execute(
            "INSERT INTO eval_results (id, eval_run_id, eval_case_id, question, answer, "
            "retrieved_sources, retrieval_metrics, answer_metrics, ragas_metrics, "
            "type_statuses, score, passed, latency_ms, created_at) VALUES "
            "('res-1', 'run-1', 'case-1', 'q', 'a', '[]'::json, '{}'::json, '{}'::json, "
            "'{}'::json, '{}'::json, 1.0, true, 5, now())"
        )


def _stage(pg_url: str, name: str, *steps: str) -> Iterator[str]:
    """Build a private database by running ``steps`` in order and yield its URL.

    ``steps`` are ``"expand"``, ``"seed"``, ``"backfill"`` or ``"enforce"``, so a
    test states the exact migration state it needs instead of inheriting it.
    """
    with scratch_database(pg_url, name) as url:
        for step in steps:
            if step == "expand":
                result = alembic_upgrade(url, "001")
                assert result.returncode == 0, result.stderr
            elif step == "seed":
                seed_legacy_rows(url)
            elif step == "backfill":
                result = run_legacy_backfill(url)
                assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
            elif step == "enforce":
                result = alembic_upgrade(url, "head")
                assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
            else:  # pragma: no cover - guards a typo in a test's step list
                raise AssertionError(f"unknown stage step {step!r}")
        yield url


@pytest.fixture(scope="module")
def expanded(pg_url: str) -> Iterator[str]:
    """A database with only the additive expand revision applied."""
    yield from _stage(pg_url, "pf_it_expanded", "expand")


@pytest.fixture(scope="module")
def reconciled(pg_url: str) -> Iterator[str]:
    """A database whose expand revision has been reconciled by the backfill."""
    yield from _stage(pg_url, "pf_it_reconciled", "expand", "seed", "backfill")


@pytest.fixture(scope="module")
def enforced(pg_url: str) -> Iterator[str]:
    """A fully migrated database: expand, backfill, then enforce."""
    yield from _stage(pg_url, "pf_it_enforced", "expand", "seed", "backfill", "enforce")


def test_expand_is_additive_and_leaves_ownership_nullable(expanded: str) -> None:
    """001 must only widen the schema; it may not enforce ownership."""
    with connect(expanded) as cursor:
        assert scalar(cursor, "SELECT version_num FROM alembic_version") == "001"
        # The legacy tenant exists so the backfill has an owner to fall back to.
        assert (
            scalar(cursor, "SELECT code FROM tenants WHERE id = %s", (LEGACY_TENANT_ID,))
            == "legacy"
        )
        nullable = rows(
            cursor,
            "SELECT table_name FROM information_schema.columns WHERE table_schema = "
            "'public' AND column_name = 'tenant_id' AND is_nullable = 'YES' "
            "ORDER BY table_name",
        )
        assert nullable, "expand must leave tenant_id nullable for the backfill window"
        # Row-level security exists but is deliberately not yet forced.
        not_forced = rows(
            cursor,
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity "
            "AND NOT c.relforcerowsecurity ORDER BY relname",
        )
        assert not_forced, "expand enables RLS without forcing it"
        checks = scalar(
            cursor,
            "SELECT count(*) FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND con.contype = 'c'",
        )
        assert int(checks) >= 7, "enum-like columns must be constrained in the database"


def test_enforce_refuses_before_the_backfill_has_run(expanded: str) -> None:
    """The guard is the point of the enforce phase: it must actually block."""
    seed_legacy_rows(expanded)

    refused = alembic_upgrade(expanded, "head")
    assert refused.returncode != 0, "enforce must not run without a reconciled backfill"
    combined = f"{refused.stdout}\n{refused.stderr}"
    assert "enforce refused" in combined
    assert "backfill" in combined
    with connect(expanded) as cursor:
        assert scalar(cursor, "SELECT version_num FROM alembic_version") == "001"


def test_second_enforce_revision_is_idempotent_once_applied(enforced: str) -> None:
    """Re-running the chain after enforce must be a no-op, not a failure."""
    again = alembic_upgrade(enforced, "head")
    assert again.returncode == 0, f"{again.stdout}\n{again.stderr}"
    with connect(enforced) as cursor:
        assert scalar(cursor, "SELECT version_num FROM alembic_version") == "002"


def test_interrupted_backfill_resumes_and_reconciles(pg_url: str) -> None:
    """A stopped run must resume from its cursor and reach the same state.

    This is the restartability contract: proofs of an interrupted migration are
    worthless if the retry silently re-does work or, worse, silently skips it.
    """
    with scratch_database(pg_url, "pf_it_resume") as url:
        assert alembic_upgrade(url, "001").returncode == 0
        seed_legacy_rows(url)

        first = run_backfill_cli(url, batch_size=2, stop_after_batches=1)
        assert first.returncode == 0, first.stderr
        assert "stopped_early=True" in first.stdout, first.stdout

        with connect(url) as cursor:
            unfinished = rows(
                cursor,
                "SELECT table_name, status, cursor_value FROM migration_backfill_state "
                "WHERE status <> 'completed'",
            )
            assert unfinished, "an interrupted run must leave persisted progress"
            stopped_cursors = {name: value for name, _status, value in unfinished}

        resumed = run_legacy_backfill(url)
        assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"
        assert "stopped_early=False" in resumed.stdout

        with connect(url) as cursor:
            incomplete = rows(
                cursor,
                "SELECT table_name FROM migration_backfill_state WHERE status <> 'completed'",
            )
            assert incomplete == [], f"every table must reconcile, got {incomplete}"
            # The cursor advanced past where the interrupted run stopped.
            for table, stopped_at in stopped_cursors.items():
                final = scalar(
                    cursor,
                    "SELECT cursor_value FROM migration_backfill_state WHERE table_name = %s",
                    (table,),
                )
                assert final is not None, f"{table} lost its cursor"
                if stopped_at is not None:
                    assert final >= stopped_at, f"{table} cursor went backwards"
            # Attribution actually happened: children inherited the parent's tenant.
            for table in ("conversations", "messages", "users", "eval_cases"):
                unowned = scalar(cursor, f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
                assert unowned == 0, f"{table} still has {unowned} unowned rows"
            assert scalar(cursor, "SELECT tenant_id FROM messages WHERE id = 'msg-0'") == (
                LEGACY_TENANT_ID
            )

        # A second full run is a no-op, which is what makes the retry safe.
        again = run_backfill_cli(url)
        assert again.returncode == 0, again.stderr
        assert "batches=0" in again.stdout, again.stdout


def test_enforce_applies_after_reconciliation(enforced: str) -> None:
    """After the backfill, enforce tightens ownership and protects every tenant."""
    with connect(enforced) as cursor:
        assert scalar(cursor, "SELECT version_num FROM alembic_version") == "002"
        # No tenant-scoped table may still accept a row without an owner.
        nullable = rows(
            cursor,
            "SELECT table_name FROM information_schema.columns WHERE table_schema = "
            "'public' AND column_name = 'tenant_id' AND is_nullable = 'YES'",
        )
        assert nullable == [], f"tenant_id must be NOT NULL everywhere, null on {nullable}"
        # RLS is forced, so even the owning migration role is constrained.
        not_forced = rows(
            cursor,
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity "
            "AND NOT c.relforcerowsecurity ORDER BY relname",
        )
        assert not_forced == [], f"RLS must be forced everywhere, missing on {not_forced}"
        policies = int(
            scalar(
                cursor,
                "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
                "AND policyname = 'tenant_isolation'",
            )
        )
        assert policies >= 15, f"every tenant-scoped table needs a policy, found {policies}"
        # The application role exists and cannot bypass RLS.
        role = rows(
            cursor,
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s",
            (APPLICATION_ROLE,),
        )
        assert role == [(False, False)], f"application role must not bypass RLS: {role}"
        owned = scalar(
            cursor,
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND r.rolname = %s AND c.relkind = 'r'",
            (APPLICATION_ROLE,),
        )
        assert owned == 0, "the application role must not own tables"
        # The unowned rows from the legacy seed were attributed, not dropped.
        assert (
            scalar(cursor, "SELECT count(*) FROM messages WHERE tenant_id = %s", (LEGACY_TENANT_ID,))
            == 3
        )


def test_enforce_refuses_when_rows_remain_unowned(pg_url: str) -> None:
    """An unowned row must block enforce rather than be stranded by it.

    The ledger is deliberately left reconciled and the nullable column restored,
    so this exercises the second guard (unowned rows), not the missing-ledger one.
    """
    with scratch_database(pg_url, "pf_it_unowned") as url:
        assert alembic_upgrade(url, "001").returncode == 0
        seed_legacy_rows(url)
        assert run_legacy_backfill(url).returncode == 0

        with connect(url) as cursor:
            cursor.execute("ALTER TABLE conversations ALTER COLUMN tenant_id DROP NOT NULL")
            cursor.execute(
                "INSERT INTO conversations (id, tenant_id, user_id, title, channel, status, "
                "created_at, updated_at) VALUES ('conv-unowned', NULL, 'user-1', 'orphan', "
                "'web', 'active', now(), now())"
            )

        refused = alembic_upgrade(url, "head")
        assert refused.returncode != 0, "an unowned row must block enforce"
        combined = f"{refused.stdout}\n{refused.stderr}"
        assert "enforce refused" in combined
        assert "conversations=1" in combined, combined
        with connect(url) as cursor:
            assert scalar(cursor, "SELECT version_num FROM alembic_version") == "001"

        # Once the row is attributed, the same upgrade succeeds.
        with connect(url) as cursor:
            cursor.execute(
                "UPDATE conversations SET tenant_id = %s WHERE id = 'conv-unowned'",
                (LEGACY_TENANT_ID,),
            )
        applied = alembic_upgrade(url, "head")
        assert applied.returncode == 0, f"{applied.stdout}\n{applied.stderr}"


def test_row_level_security_is_selective_for_the_application_role(enforced: str) -> None:
    """RLS must separate tenants, not merely deny everything.

    A policy that always returns zero rows would pass a naive "no leak" check
    while breaking the product, so the positive case is asserted too.
    """
    with connect(enforced) as cursor:
        cursor.execute(f"SET ROLE {APPLICATION_ROLE}")
        try:
            assert scalar(cursor, "SELECT current_user") == APPLICATION_ROLE

            def visible(tenant: str) -> int:
                cursor.execute("SELECT set_config(%s, %s, false)", (TENANT_SETTING, tenant))
                return int(scalar(cursor, "SELECT count(*) FROM conversations"))

            assert visible(LEGACY_TENANT_ID) >= 1, "the owner tenant must see its own rows"
            assert visible(UNRELATED_TENANT_ID) == 0, "another tenant must see nothing"
            # Unset must mean "no rows", never "all rows".
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            assert scalar(cursor, "SELECT count(*) FROM conversations") == 0

            # A cross-tenant write must be blocked, not merely hidden on read.
            cursor.execute(
                "SELECT set_config(%s, %s, false)", (TENANT_SETTING, UNRELATED_TENANT_ID)
            )
            cursor.execute("UPDATE conversations SET title = 'stolen' WHERE id = 'conv-1'")
            assert cursor.rowcount == 0, "RLS must block cross-tenant writes, not just reads"

            # A cross-tenant insert must be rejected by the WITH CHECK clause.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(
                    "INSERT INTO conversations (id, tenant_id, user_id, title, channel, "
                    "status, created_at, updated_at) VALUES ('conv-forged', %s, 'user-1', "
                    "'forged', 'web', 'active', now(), now())",
                    (LEGACY_TENANT_ID,),
                )
        finally:
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            cursor.execute("RESET ROLE")

    with connect(enforced) as cursor:
        assert (
            scalar(cursor, "SELECT title FROM conversations WHERE id = 'conv-1'") == "Legacy chat"
        ), "the row must be unchanged after the blocked cross-tenant update"
        assert scalar(cursor, "SELECT count(*) FROM conversations WHERE id = 'conv-forged'") == 0


def test_business_codes_are_unique_per_tenant(enforced: str) -> None:
    """Two tenants must both be able to define the same business code."""
    with connect(enforced) as cursor:
        names = [
            name
            for (name,) in rows(
                cursor,
                "SELECT conname FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                "AND c.relname = 'knowledge_bases' AND con.contype = 'u'",
            )
        ]
        assert "uq_knowledge_bases_tenant_code" in names, names
        assert "knowledge_bases_code_key" not in names, "global code uniqueness must be gone"
        # The expand migration expressed the global rule as a unique *index*, so
        # leaving it behind would keep enforcing it invisibly.
        leftover_index = rows(
            cursor,
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'knowledge_bases' AND indexname = 'ix_knowledge_bases_code'",
        )
        assert leftover_index == [], "the global unique index on code must be dropped"

        cursor.execute(
            "INSERT INTO tenants (id, code, name, status, created_at, updated_at) VALUES "
            "('tenant-a', 'alpha', 'Alpha', 'active', now(), now()), "
            "('tenant-b', 'beta', 'Beta', 'active', now(), now())"
        )
        cursor.execute(
            "INSERT INTO departments (id, code, name, created_at) VALUES "
            "('dept-1', 'dept-one', 'Department One', now())"
        )
        for tenant in ("tenant-a", "tenant-b"):
            cursor.execute(
                "INSERT INTO knowledge_bases (id, tenant_id, name, code, department_id, "
                "description, rag_workspace, default_query_mode, status, created_by, "
                "created_at, updated_at) VALUES (%s, %s, 'Finance', 'finance', 'dept-1', "
                "'', %s, 'mix', 'active', 'user-1', now(), now())",
                (f"kb-{tenant}", tenant, f"workspaces/{tenant}"),
            )
        # Repeating the code inside one tenant is still rejected.
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute(
                "INSERT INTO knowledge_bases (id, tenant_id, name, code, department_id, "
                "description, rag_workspace, default_query_mode, status, created_by, "
                "created_at, updated_at) VALUES ('kb-dup', 'tenant-a', 'Other', 'finance', "
                "'dept-1', '', 'workspaces/dup', 'mix', 'active', 'user-1', now(), now())"
            )
