"""T066 [US2] the PostgreSQL side of quota admission on the production authority.

The Redis coordinator (:mod:`backend.app.quota.coordinator`) makes the atomic
per-request decision, but it is *seeded* by numbers PostgreSQL owns and it leaves
no durable trace. :class:`~backend.app.quota.ledger.QuotaLedger` is that missing
authority:

* it resolves the governing :class:`QuotaPolicy` for a ``(tenant, workload)``
  from PostgreSQL, most-specific tier first, so admission limits are configured
  in the database rather than hard-coded at the call site;
* it writes a durable :class:`QuotaLease` audit row when a Redis lease is taken
  and closes it (``released_at`` + ``outcome``) when the slot is returned or
  expires -- idempotently, so a redelivered release never rewrites the outcome;
* it appends :class:`UsageRecord` rows (reserved at admission, actual at
  completion), never editing, so an over-reservation trued-up later cannot
  corrupt the billed figure.

These are proven on a real PostgreSQL because the resolution order, the
append-only guarantee and the null-tolerant global policy are exactly the
semantics SQLite's schema cannot vouch for. The suite skips cleanly when no
server is reachable (via the shared ``pg_url`` fixture); the Stage-4 tables are
built with ``metadata.create_all`` on a throwaway database, mirroring the T063
lease test -- the production Alembic path (revision 003) is proven separately in
``test_stage4_migration.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import Tenant, utc_now
from backend.app.db.session import build_async_engine
from backend.app.quota.ledger import QuotaLedger
from tests import conftest

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest_asyncio.fixture
async def ledger(pg_url: str) -> AsyncIterator[QuotaLedger]:
    """A QuotaLedger over a throwaway PostgreSQL database with two tenants."""
    with conftest.scratch_database(pg_url, "pf_it_quota_ledger") as url:
        engine = build_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            session.add(Tenant(id=TENANT_A, code="alpha", name="Alpha", status="active"))
            session.add(Tenant(id=TENANT_B, code="beta", name="Beta", status="active"))
            await session.commit()
        try:
            yield QuotaLedger(factory=factory)
        finally:
            await engine.dispose()


async def _add_policy(
    ledger: QuotaLedger,
    *,
    scope: str,
    tenant_id: str | None,
    workload: str | None,
    max_concurrency: int,
    version: int = 1,
    active: bool = True,
    tokens_per_window: int = 1000,
    requests_per_window: int = 100,
) -> str:
    return await ledger.upsert_policy(
        scope=scope,
        tenant_id=tenant_id,
        workload=workload,
        requests_per_window=requests_per_window,
        window_seconds=60,
        tokens_per_window=tokens_per_window,
        max_concurrency=max_concurrency,
        queue_admission_limit=max_concurrency * 2,
        version=version,
        active=active,
    )


# -- policy resolution -------------------------------------------------------


async def test_resolve_prefers_most_specific_active_policy(ledger: QuotaLedger) -> None:
    """tenant+workload beats tenant beats global+workload beats global."""
    await _add_policy(ledger, scope="global", tenant_id=None, workload=None, max_concurrency=1)
    await _add_policy(ledger, scope="global", tenant_id=None, workload="chat", max_concurrency=2)
    await _add_policy(ledger, scope="tenant", tenant_id=TENANT_A, workload=None, max_concurrency=3)
    await _add_policy(
        ledger, scope="tenant", tenant_id=TENANT_A, workload="chat", max_concurrency=4
    )

    resolved = await ledger.resolve_policy(tenant_id=TENANT_A, workload="chat")
    assert resolved is not None
    assert resolved.max_concurrency == 4  # tenant + workload wins

    # Drop the most specific tier by asking for a workload only the tenant tier covers.
    resolved = await ledger.resolve_policy(tenant_id=TENANT_A, workload="eval")
    assert resolved is not None and resolved.max_concurrency == 3  # tenant, workload-agnostic


async def test_resolve_falls_back_to_global_for_unknown_tenant(ledger: QuotaLedger) -> None:
    """A tenant with no policy of its own is governed by the global tier."""
    await _add_policy(ledger, scope="global", tenant_id=None, workload=None, max_concurrency=7)
    await _add_policy(ledger, scope="global", tenant_id=None, workload="chat", max_concurrency=9)

    assert (await ledger.resolve_policy(tenant_id=TENANT_B, workload="chat")).max_concurrency == 9
    assert (await ledger.resolve_policy(tenant_id=TENANT_B, workload="other")).max_concurrency == 7


async def test_resolve_ignores_inactive_and_prefers_highest_version(
    ledger: QuotaLedger,
) -> None:
    """Only ``active`` rows count; among active rows the highest version wins."""
    await _add_policy(
        ledger, scope="tenant", tenant_id=TENANT_A, workload="chat",
        max_concurrency=5, version=1, active=False,
    )
    await _add_policy(
        ledger, scope="tenant", tenant_id=TENANT_A, workload="chat",
        max_concurrency=6, version=2, active=True,
    )
    resolved = await ledger.resolve_policy(tenant_id=TENANT_A, workload="chat")
    assert resolved is not None and resolved.version == 2 and resolved.max_concurrency == 6


async def test_resolve_returns_none_when_nothing_matches(ledger: QuotaLedger) -> None:
    assert await ledger.resolve_policy(tenant_id=TENANT_A, workload="chat") is None


# -- lease audit -------------------------------------------------------------


async def test_lease_audit_records_and_closes_idempotently(ledger: QuotaLedger) -> None:
    """A taken slot writes an open lease; closing stamps outcome once."""
    expires = utc_now() + timedelta(seconds=60)
    lease = await ledger.record_lease(
        tenant_id=TENANT_A, resource="run", owner="redis-lease-1",
        expires_at=expires, run_id="run-1",
    )
    assert lease.released_at is None and lease.outcome is None

    closed = await ledger.close_lease(lease_id=lease.id, outcome="released")
    assert closed.released_at is not None and closed.outcome == "released"
    first_release = closed.released_at

    # A redelivered release must not rewrite the recorded outcome or timestamp.
    again = await ledger.close_lease(lease_id=lease.id, outcome="expired")
    assert again.outcome == "released" and again.released_at == first_release


async def test_lease_audit_is_queryable_by_redis_owner(ledger: QuotaLedger) -> None:
    """The audit row is keyed by the Redis lease id so the two can be correlated."""
    expires = utc_now() + timedelta(seconds=60)
    await ledger.record_lease(
        tenant_id=TENANT_A, resource="run", owner="redis-lease-xyz", expires_at=expires
    )
    open_leases = await ledger.open_leases(tenant_id=TENANT_A)
    assert [row.owner for row in open_leases] == ["redis-lease-xyz"]


# -- usage writeback ---------------------------------------------------------


async def test_usage_is_append_only_and_separates_reserved_from_actual(
    ledger: QuotaLedger,
) -> None:
    """Reserved (admission) and actual (completion) accumulate without editing."""
    await ledger.record_usage(
        tenant_id=TENANT_A, run_id="run-9", kind="reserved", requests=1, reserved_tokens=500,
    )
    await ledger.record_usage(
        tenant_id=TENANT_A, run_id="run-9", kind="actual",
        requests=1, actual_tokens=320, latency_ms=1200, provider="mock", model="det-mock",
    )
    totals = await ledger.usage_totals(tenant_id=TENANT_A, run_id="run-9")
    assert totals["records"] == 2
    assert totals["reserved_tokens"] == 500
    assert totals["actual_tokens"] == 320
    assert totals["requests"] == 2


async def test_usage_is_tenant_scoped(ledger: QuotaLedger) -> None:
    """Usage totals never bleed across tenants."""
    await ledger.record_usage(tenant_id=TENANT_A, run_id="r", kind="actual", actual_tokens=10)
    await ledger.record_usage(tenant_id=TENANT_B, run_id="r", kind="actual", actual_tokens=99)
    assert (await ledger.usage_totals(tenant_id=TENANT_A))["actual_tokens"] == 10
    assert (await ledger.usage_totals(tenant_id=TENANT_B))["actual_tokens"] == 99
