"""Integration tests for the Stage-4 concurrency/durability migration (003).

The Stage-4 tables (``durable_jobs``, ``outbox_events``, ``quota_policies``,
``quota_leases``, ``usage_records``, ``capacity_test_runs``) are the durable
authority for User Story 2 -- runs, quota and outbox delivery that survive a
restart. Their claim/lease logic is proven elsewhere on a schema built with
``metadata.create_all`` (T063, T068), but that path is not what production runs:
production runs ``alembic upgrade head``. Until 003 existed, ``upgrade head``
stopped at 002 and never created these tables -- an honest gap between the tests'
schema and the deployed one.

This suite closes that gap by driving the real migration chain to ``head`` on a
throwaway PostgreSQL database and asserting, against the live catalog, that:

* all six Stage-4 tables exist at revision ``003``;
* the five tenant-scoped tables have **forced** row-level security and a
  ``tenant_isolation`` policy -- i.e. even the table owner is constrained, the
  same guarantee 002 gives the Stage-2 tables;
* ``capacity_test_runs`` (which measures the platform, not a customer) has no
  ``tenant_id`` and no RLS;
* the restricted ``policyflow_app`` role genuinely cannot read or write across
  tenants on ``durable_jobs``, while a global (null-tenant) ``quota_policies``
  row stays visible to every tenant.

Nothing is simulated: every assertion reads the live schema or the live
behaviour of the restricted application role. Skipped cleanly when no PostgreSQL
is reachable (via the shared ``pg_url`` fixture).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from sqlalchemy.engine import make_url

from tests.conftest import alembic_upgrade, run_legacy_backfill, scratch_database

APPLICATION_ROLE = "policyflow_app"
TENANT_SETTING = "policyflow.tenant_id"
TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

#: Every Stage-4 table 003 must create.
STAGE4_TABLES: tuple[str, ...] = (
    "durable_jobs",
    "outbox_events",
    "quota_policies",
    "quota_leases",
    "usage_records",
    "capacity_test_runs",
)

#: The Stage-4 tables that carry ``tenant_id`` and must be RLS-protected.
STAGE4_TENANT_TABLES: tuple[str, ...] = (
    "durable_jobs",
    "outbox_events",
    "quota_policies",
    "quota_leases",
    "usage_records",
)


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def connect(url: str) -> Iterator[psycopg.Cursor]:
    with psycopg.connect(_dsn(url), autocommit=True) as conn, conn.cursor() as cursor:
        yield cursor


def scalar(cursor: psycopg.Cursor, sql: str, params: tuple[object, ...] = ()) -> object:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return None if row is None else row[0]


@pytest.fixture(scope="module")
def stage4(pg_url: str) -> Iterator[str]:
    """A throwaway database migrated all the way to head (through 003).

    The chain is applied exactly as production must: additive expand, the
    legacy-tenant backfill, then ``upgrade head`` (which now reaches 003). A
    private database is used because these tests drive the chain themselves.
    """
    with scratch_database(pg_url, "pf_it_stage4") as url:
        expand = alembic_upgrade(url, "001")
        assert expand.returncode == 0, f"{expand.stdout}\n{expand.stderr}"
        backfill = run_legacy_backfill(url)
        assert backfill.returncode == 0, f"{backfill.stdout}\n{backfill.stderr}"
        head = alembic_upgrade(url, "head")
        assert head.returncode == 0, f"{head.stdout}\n{head.stderr}"
        # Two tenants to exercise cross-tenant isolation on the new tables.
        with connect(url) as cursor:
            cursor.execute(
                "INSERT INTO tenants (id, code, name, status, created_at, updated_at) "
                "VALUES (%s, 'alpha4', 'Alpha', 'active', now(), now()), "
                "(%s, 'beta4', 'Beta', 'active', now(), now())",
                (TENANT_A, TENANT_B),
            )
        yield url


def _insert_durable_job(cursor: psycopg.Cursor, job_id: str, tenant_id: str, key: str) -> None:
    cursor.execute(
        "INSERT INTO durable_jobs (id, tenant_id, kind, idempotency_key, "
        "payload_schema_version, payload_digest, payload, state, priority_lane, "
        "attempts, max_attempts, available_at, created_at, updated_at, version) VALUES "
        "(%s, %s, 'kb_reindex', %s, 1, 'digest', '{}'::json, 'queued', 'default', "
        "0, 5, now(), now(), now(), 1)",
        (job_id, tenant_id, key),
    )


def test_head_is_revision_003_with_all_stage4_tables(stage4: str) -> None:
    """``upgrade head`` must reach 003 and create every Stage-4 table."""
    with connect(stage4) as cursor:
        assert scalar(cursor, "SELECT version_num FROM alembic_version") == "003"
        for table in STAGE4_TABLES:
            exists = scalar(
                cursor, "SELECT to_regclass(%s)", (f"public.{table}",)
            )
            assert exists is not None, f"003 must create {table}"


def test_stage4_tenant_tables_have_forced_rls_and_policy(stage4: str) -> None:
    """Every tenant-scoped Stage-4 table must have forced RLS + a policy.

    Forced (not merely enabled) is the point: the migration role owns these
    tables, and an owner bypasses an unforced policy, so an enabled-but-unforced
    table would be silently unisolated for exactly the durable state that must
    not leak across tenants.
    """
    with connect(stage4) as cursor:
        for table in STAGE4_TENANT_TABLES:
            cursor.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = %s",
                (table,),
            )
            enabled, forced = cursor.fetchone()
            assert enabled and forced, f"{table} must have forced RLS, got {(enabled, forced)}"
            policies = int(
                scalar(
                    cursor,
                    "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
                    "AND tablename = %s AND policyname = 'tenant_isolation'",
                    (table,),
                )
            )
            assert policies == 1, f"{table} needs a tenant_isolation policy, found {policies}"


def test_capacity_test_runs_is_not_tenant_protected(stage4: str) -> None:
    """A capacity run measures the platform, so it is global, not RLS-scoped."""
    with connect(stage4) as cursor:
        has_tenant = scalar(
            cursor,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
            "'public' AND table_name = 'capacity_test_runs' AND column_name = 'tenant_id'",
        )
        assert int(has_tenant) == 0, "capacity_test_runs must not carry tenant_id"
        secured = scalar(
            cursor,
            "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            "AND c.relname = 'capacity_test_runs'",
        )
        assert secured is False, "capacity_test_runs must not have RLS enabled"
        policies = int(
            scalar(
                cursor,
                "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = 'capacity_test_runs'",
            )
        )
        assert policies == 0, "capacity_test_runs must carry no policy"


def test_durable_jobs_isolates_tenants_for_the_application_role(stage4: str) -> None:
    """The restricted role must see and write only its own tenant's jobs."""
    with connect(stage4) as cursor:
        _insert_durable_job(cursor, "job-a", TENANT_A, "idem-key-tenant-a-0001")

        cursor.execute(f"SET ROLE {APPLICATION_ROLE}")
        try:
            assert scalar(cursor, "SELECT current_user") == APPLICATION_ROLE

            def visible(tenant: str) -> int:
                cursor.execute("SELECT set_config(%s, %s, false)", (TENANT_SETTING, tenant))
                return int(scalar(cursor, "SELECT count(*) FROM durable_jobs"))

            assert visible(TENANT_A) == 1, "the owning tenant must see its own job"
            assert visible(TENANT_B) == 0, "another tenant must see nothing"
            # Unset tenant must mean "no rows", never "all rows".
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            assert scalar(cursor, "SELECT count(*) FROM durable_jobs") == 0

            # A cross-tenant insert must be rejected by the WITH CHECK clause.
            cursor.execute("SELECT set_config(%s, %s, false)", (TENANT_SETTING, TENANT_B))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _insert_durable_job(cursor, "job-forged", TENANT_A, "idem-key-forged-0001")
        finally:
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            cursor.execute("RESET ROLE")

    with connect(stage4) as cursor:
        assert scalar(cursor, "SELECT count(*) FROM durable_jobs WHERE id = 'job-forged'") == 0


def test_quota_policies_global_row_is_visible_to_every_tenant(stage4: str) -> None:
    """A null-tenant quota policy is global; a tenant-scoped one stays isolated."""
    with connect(stage4) as cursor:
        cursor.execute(
            "INSERT INTO quota_policies (id, scope, tenant_id, requests_per_window, "
            "window_seconds, tokens_per_window, max_concurrency, queue_admission_limit, "
            "active, created_at, updated_at, version) VALUES "
            "('qp-global', 'global', NULL, 100, 60, 0, 0, 0, true, now(), now(), 1)"
        )
        cursor.execute(
            "INSERT INTO quota_policies (id, scope, tenant_id, requests_per_window, "
            "window_seconds, tokens_per_window, max_concurrency, queue_admission_limit, "
            "active, created_at, updated_at, version) VALUES "
            "('qp-tenant-b', 'tenant', %s, 50, 60, 0, 0, 0, true, now(), now(), 1)",
            (TENANT_B,),
        )

        cursor.execute(f"SET ROLE {APPLICATION_ROLE}")
        try:
            cursor.execute("SELECT set_config(%s, %s, false)", (TENANT_SETTING, TENANT_A))
            # Global row visible to tenant A; tenant B's row is not.
            assert scalar(
                cursor, "SELECT count(*) FROM quota_policies WHERE id = 'qp-global'"
            ) == 1
            assert scalar(
                cursor, "SELECT count(*) FROM quota_policies WHERE id = 'qp-tenant-b'"
            ) == 0
        finally:
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            cursor.execute("RESET ROLE")
