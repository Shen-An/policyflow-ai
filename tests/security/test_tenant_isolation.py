"""Tenant isolation tests: repository predicates, RLS, and identifier collisions.

Tenant isolation is a security property, so these tests check it in the two
places it must hold simultaneously:

* the **application** layer, where every protected repository call takes an
  explicit tenant and a cross-tenant lookup must be indistinguishable from a
  missing row;
* the **database** layer, where row-level security is a second line of defence
  in case a predicate is ever forgotten.

Row-level security is forced on the tables, but a PostgreSQL superuser bypasses
RLS regardless, so the RLS assertions deliberately run as the restricted
``policyflow_app`` role. Without that, they would pass while proving nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.db.repositories import (
    ResourceNotFoundError,
    TenantScopeError,
    UnitOfWork,
    VersionConflictError,
)
from backend.app.db.session import build_async_engine

APPLICATION_ROLE = "policyflow_app"
TENANT_SETTING = "policyflow.tenant_id"


def dsn(url: str) -> str:
    """Return a libpq DSN for an async-style SQLAlchemy URL."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def connect(url: str) -> Iterator[psycopg.Cursor]:
    """Yield an autocommit cursor against ``url``."""
    with psycopg.connect(dsn(url), autocommit=True) as conn, conn.cursor() as cursor:
        yield cursor


def scalar(cursor: psycopg.Cursor, sql: str, params: tuple[object, ...] = ()) -> object:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return None if row is None else row[0]


@pytest.fixture(scope="module")
def isolated(pg_url: str) -> Iterator[str]:
    """A private migrated database plus two tenants that must not see each other."""
    from tests import conftest

    with conftest.scratch_database(pg_url, "pf_sec_isolation") as url:
        # expand -> backfill -> enforce, which is the only order enforce permits.
        expand = conftest.alembic_upgrade(url, "001")
        assert expand.returncode == 0, expand.stderr
        backfill = conftest.run_legacy_backfill(url)
        assert backfill.returncode == 0, backfill.stderr
        head = conftest.alembic_upgrade(url, "head")
        assert head.returncode == 0, head.stderr

        with connect(url) as cursor:
            cursor.execute(
                "INSERT INTO tenants (id, code, name, status, created_at, updated_at) "
                "VALUES ('tenant-alpha', 'alpha', 'Alpha', 'active', now(), now()), "
                "('tenant-beta', 'beta', 'Beta', 'active', now(), now())"
            )
            # Both tenants deliberately reuse the SAME business identifiers. This
            # is legal per-tenant data, and it is what makes a missing tenant
            # predicate visible rather than accidentally correct.
            cursor.execute(
                "INSERT INTO departments (id, code, name, created_at) "
                "VALUES ('dept-shared', 'shared', 'Shared', now())"
            )
            for suffix, tenant in (("a", "tenant-alpha"), ("b", "tenant-beta")):
                cursor.execute(
                    "INSERT INTO knowledge_bases (id, tenant_id, name, code, department_id, "
                    "description, rag_workspace, default_query_mode, status, created_by, "
                    "created_at, updated_at) VALUES (%s, %s, 'Finance', 'finance', "
                    "'dept-shared', '', %s, 'mix', 'active', 'seed', now(), now())",
                    (f"kb-{suffix}", tenant, f"ws/{suffix}"),
                )
                cursor.execute(
                    "INSERT INTO users (id, tenant_id, username, email, password_hash, "
                    "display_name, status, created_at, updated_at) VALUES "
                    "(%s, %s, 'shared-user', %s, 'x', 'Shared', 'active', now(), now())",
                    (f"user-{suffix}", tenant, f"{suffix}@example.com"),
                )
                cursor.execute(
                    "INSERT INTO conversations (id, tenant_id, user_id, title, channel, "
                    "status, created_at, updated_at) VALUES (%s, %s, %s, %s, 'web', "
                    "'active', now(), now())",
                    (f"conv-{suffix}", tenant, f"user-{suffix}", f"Chat {suffix}"),
                )
        yield url


@pytest.fixture
def uow_factory(isolated: str) -> Iterator[async_sessionmaker]:
    """An async session factory bound to the isolated database."""
    engine = build_async_engine(isolated)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def test_repository_reads_are_tenant_scoped(uow_factory: async_sessionmaker) -> None:
    """A row owned by another tenant must not be readable, even by known id."""
    async with UnitOfWork(factory=uow_factory) as uow:
        # The own-tenant read proves the predicate is not simply rejecting all ids.
        own = await uow.users.get("tenant-alpha", "user-a")
        assert own.id == "user-a"

        with pytest.raises(ResourceNotFoundError) as cross_tenant:
            await uow.users.get("tenant-alpha", "user-b")
        # A genuinely absent id must raise the SAME error, or the difference in
        # behaviour tells an attacker that user-b exists somewhere.
        with pytest.raises(ResourceNotFoundError) as absent:
            await uow.users.get("tenant-alpha", "user-does-not-exist")

    assert type(cross_tenant.value) is type(absent.value)
    # The *shape* of the refusal must be identical: same error class, same code,
    # same status. The message echoes the id the caller already supplied, so it
    # cannot be an oracle, but it must never mention the owning tenant.
    assert cross_tenant.value.code == absent.value.code
    assert cross_tenant.value.status_code == absent.value.status_code
    for error in (cross_tenant.value, absent.value):
        assert "tenant-alpha" not in str(error)
        assert "tenant-beta" not in str(error)


async def test_repository_rejects_a_missing_tenant_instead_of_defaulting(
    uow_factory: async_sessionmaker,
) -> None:
    """An absent tenant must fail closed, never fall back to "all tenants"."""
    async with UnitOfWork(factory=uow_factory) as uow:
        # ``tenant_supplied`` distinguishes "argument forgotten" from "empty
        # string passed", which are different defects with different fixes.
        for empty, supplied in ((None, False), ("", True)):
            with pytest.raises(TenantScopeError) as excinfo:
                await uow.users.get(empty, "user-a")  # type: ignore[arg-type]
            # The operation is structured detail rather than message text, so the
            # error contract stays stable while still naming the failing call.
            assert excinfo.value.details == {
                "operation": "UserRepository.get",
                "tenant_supplied": supplied,
            }, excinfo.value.details


async def test_listing_never_crosses_the_tenant_boundary(uow_factory: async_sessionmaker) -> None:
    """Enumeration is the easiest place to leak; it must be scoped too."""
    async with UnitOfWork(factory=uow_factory) as uow:
        alpha = await uow.runs.list_for_tenant("tenant-alpha")
        beta = await uow.runs.list_for_tenant("tenant-beta")
    assert alpha == []
    assert beta == []


async def test_resource_catalog_answers_per_tenant(uow_factory: async_sessionmaker) -> None:
    """Presence checks must not confirm another tenant's resource."""
    async with UnitOfWork(factory=uow_factory) as uow:
        catalog = uow.resource_catalog
        assert await catalog.contains("tenant-alpha", "user", "user-a") is True
        assert await catalog.contains("tenant-alpha", "user", "user-b") is False
        assert await catalog.contains("tenant-beta", "user", "user-b") is True
        # A tenant is visible only to itself.
        assert await catalog.contains("tenant-alpha", "tenant", "tenant-alpha") is True
        assert await catalog.contains("tenant-alpha", "tenant", "tenant-beta") is False


async def test_role_codes_are_unique_per_tenant_only(uow_factory: async_sessionmaker) -> None:
    """The same role code must be usable in two tenants but not twice in one."""
    async with UnitOfWork(factory=uow_factory) as uow:
        await uow.roles.create("tenant-alpha", code="auditor", name="Auditor")
        await uow.roles.create("tenant-beta", code="auditor", name="Auditor")
        await uow.commit()

    async with UnitOfWork(factory=uow_factory) as uow:
        alpha = await uow.roles.get_by_code("tenant-alpha", "auditor")
        beta = await uow.roles.get_by_code("tenant-beta", "auditor")
        # Same code, different rows: a global unique key would have made the
        # second create fail, and a missing predicate would have returned one row
        # for both lookups.
        assert alpha.id != beta.id
        assert alpha.tenant_id == "tenant-alpha"
        assert beta.tenant_id == "tenant-beta"

    # A genuine duplicate inside one tenant is still rejected by the database.
    async with UnitOfWork(factory=uow_factory) as uow:
        with pytest.raises(IntegrityError):
            await uow.roles.create("tenant-alpha", code="auditor", name="Duplicate")


async def test_compare_and_set_blocks_a_stale_write(uow_factory: async_sessionmaker) -> None:
    """Two writers built from the same snapshot must not both succeed."""
    async with UnitOfWork(factory=uow_factory) as uow:
        created = await uow.users.create(
            "tenant-alpha",
            username="cas-user",
            email="cas@example.com",
            password_hash="x",
            display_name="CAS",
        )
        user_id = created.id
        stale_version = created.version
        await uow.commit()

    # First writer wins and advances the version.
    async with UnitOfWork(factory=uow_factory) as uow:
        updated = await uow.users.set_status(
            "tenant-alpha", user_id, "suspended", expected_version=stale_version
        )
        assert updated.version == stale_version + 1
        await uow.commit()

    # The second writer still holds the original version, so its write must be
    # refused rather than silently overwriting the first one.
    async with UnitOfWork(factory=uow_factory) as uow:
        with pytest.raises(VersionConflictError):
            await uow.users.set_status(
                "tenant-alpha", user_id, "active", expected_version=stale_version
            )

    async with UnitOfWork(factory=uow_factory) as uow:
        current = await uow.users.get("tenant-alpha", user_id)
        assert current.status == "suspended", "the lost update must not have been applied"
        assert current.version == stale_version + 1


def test_row_level_security_is_forced_and_selective(isolated: str) -> None:
    """RLS must separate tenants as the restricted application role.

    The positive case is asserted alongside the negative one: a policy that
    returned no rows ever would pass a naive leak check while breaking the
    product entirely.
    """
    with connect(isolated) as cursor:
        # Both flags are required. ``relrowsecurity`` alone means the owner can
        # bypass the policy; ``relforcerowsecurity`` alone means the policy is
        # never consulted at all, because FORCE does not enable row security.
        insecure = cursor.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND EXISTS (SELECT 1 FROM information_schema.columns col "
            "            WHERE col.table_schema = 'public' AND col.table_name = c.relname "
            "              AND col.column_name = 'tenant_id') "
            "AND NOT (c.relrowsecurity AND c.relforcerowsecurity) "
            "ORDER BY 1"
        ).fetchall()
        assert insecure == [], f"tenant-scoped tables without enforced RLS: {insecure}"

        # A policy that is never consulted is worse than none, because it looks
        # like protection. Every tenant-scoped table must have one.
        policyless = cursor.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND EXISTS (SELECT 1 FROM information_schema.columns col "
            "            WHERE col.table_schema = 'public' AND col.table_name = c.relname "
            "              AND col.column_name = 'tenant_id') "
            "AND NOT EXISTS (SELECT 1 FROM pg_policies p "
            "                WHERE p.schemaname = 'public' AND p.tablename = c.relname) "
            "ORDER BY 1"
        ).fetchall()
        assert policyless == [], f"tenant-scoped tables without an RLS policy: {policyless}"

        cursor.execute(f"SET ROLE {APPLICATION_ROLE}")
        try:
            assert scalar(cursor, "SELECT current_user") == APPLICATION_ROLE

            def visible(tenant: str, table: str) -> int:
                cursor.execute("SELECT set_config(%s, %s, false)", (TENANT_SETTING, tenant))
                return int(scalar(cursor, f"SELECT count(*) FROM {table}"))

            for table in ("conversations", "knowledge_bases"):
                assert visible("tenant-alpha", table) == 1, f"{table} must show alpha its own row"
                assert visible("tenant-beta", table) == 1, f"{table} must show beta its own row"
                # The rows are distinct even though their business identifiers are
                # identical, so a count of 1 cannot be a shared row.
                assert visible("tenant-alpha", table) == 1

            # With no tenant selected, RLS must expose nothing at all.
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            for table in ("conversations", "knowledge_bases"):
                assert scalar(cursor, f"SELECT count(*) FROM {table}") == 0, (
                    "an unset tenant must mean no rows, never every row"
                )

            cursor.execute("SELECT set_config(%s, 'tenant-alpha', false)", (TENANT_SETTING,))
            cursor.execute("UPDATE conversations SET title = 'stolen' WHERE id = 'conv-b'")
            assert cursor.rowcount == 0, "RLS must block cross-tenant writes"
            cursor.execute("DELETE FROM conversations WHERE id = 'conv-b'")
            assert cursor.rowcount == 0, "RLS must block cross-tenant deletes"
        finally:
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            cursor.execute("RESET ROLE")

    with connect(isolated) as cursor:
        # Nothing leaked or was destroyed by the blocked statements.
        assert scalar(cursor, "SELECT title FROM conversations WHERE id = 'conv-b'") == "Chat b"
        assert scalar(cursor, "SELECT count(*) FROM conversations") == 2


def test_identical_business_identifiers_stay_isolated(isolated: str) -> None:
    """Two tenants may hold the same code, and each read must return its own row."""
    with connect(isolated) as cursor:
        cursor.execute(f"SET ROLE {APPLICATION_ROLE}")
        try:
            cursor.execute("SELECT set_config(%s, 'tenant-alpha', false)", (TENANT_SETTING,))
            alpha = cursor.execute(
                "SELECT id, tenant_id FROM knowledge_bases WHERE code = 'finance'"
            ).fetchall()
            cursor.execute("SELECT set_config(%s, 'tenant-beta', false)", (TENANT_SETTING,))
            beta = cursor.execute(
                "SELECT id, tenant_id FROM knowledge_bases WHERE code = 'finance'"
            ).fetchall()
        finally:
            cursor.execute("SELECT set_config(%s, '', false)", (TENANT_SETTING,))
            cursor.execute("RESET ROLE")

    assert alpha == [("kb-a", "tenant-alpha")], alpha
    assert beta == [("kb-b", "tenant-beta")], beta

    with connect(isolated) as cursor:
        # The database still rejects a genuine duplicate inside one tenant, so
        # isolation did not come at the cost of uniqueness.
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute(
                "INSERT INTO knowledge_bases (id, tenant_id, name, code, department_id, "
                "description, rag_workspace, default_query_mode, status, created_by, "
                "created_at, updated_at) VALUES ('kb-dup', 'tenant-alpha', 'Finance', 'finance', "
                "'dept-shared', '', 'ws/dup', 'mix', 'active', 'seed', now(), now())"
            )
