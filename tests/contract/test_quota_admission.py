"""T058 [US2] contract for Redis-coordinated quota admission.

Overload protection is atomic and lives in Redis so every stateless API instance
sees one shared budget: a token bucket bounds request rate, a lease semaphore
bounds concurrency. Admission returns a typed decision the API maps to 200 /
429 / 503 + Retry-After. These tests run against a real Redis (single-threaded
Lua makes the check-and-consume atomic); they skip cleanly when none is running
rather than faking the coordinator.

Time is injected (``now_ms``) so refill and lease-expiry are deterministic
without wall-clock waits; production passes the real clock.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

redis_asyncio = pytest.importorskip("redis.asyncio")

from backend.app.quota.coordinator import QuotaCoordinator  # noqa: E402

REDIS_URL = os.environ.get("POLICYFLOW_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


@pytest_asyncio.fixture
async def coord() -> AsyncIterator[QuotaCoordinator]:
    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001 - any connect failure gates the suite
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    prefix = f"test:quota:{uuid.uuid4().hex}"
    try:
        yield QuotaCoordinator(client, prefix=prefix)
    finally:
        keys = [k async for k in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_token_bucket_admits_up_to_capacity_then_rate_limits(
    coord: QuotaCoordinator,
) -> None:
    kw = dict(resource="chat", identity="u:1", capacity=3, refill_per_sec=0.001,
              max_concurrency=100, lease_ms=60_000)
    for _ in range(3):
        d = await coord.admit(now_ms=0, **kw)
        assert d.admitted and d.http_status == 200
    denied = await coord.admit(now_ms=0, **kw)
    assert not denied.admitted
    assert denied.reason == "rate_limited"
    assert denied.http_status == 429
    assert denied.retry_after_seconds > 0
    assert denied.lease_id is None


async def test_token_bucket_refills_over_time(coord: QuotaCoordinator) -> None:
    kw = dict(resource="chat", identity="u:2", capacity=2, refill_per_sec=2.0,
              max_concurrency=100, lease_ms=60_000)
    assert (await coord.admit(now_ms=0, **kw)).admitted
    assert (await coord.admit(now_ms=0, **kw)).admitted
    assert not (await coord.admit(now_ms=0, **kw)).admitted
    # 1000ms * 2 tokens/s = 2 tokens refilled.
    assert (await coord.admit(now_ms=1000, **kw)).admitted


async def test_concurrency_saturates_then_releases(coord: QuotaCoordinator) -> None:
    kw = dict(resource="run", identity="t:acme", capacity=1000, refill_per_sec=1000.0,
              max_concurrency=2, lease_ms=60_000)
    a = await coord.admit(now_ms=0, **kw)
    b = await coord.admit(now_ms=0, **kw)
    assert a.admitted and b.admitted
    assert a.lease_id and b.lease_id and a.lease_id != b.lease_id
    saturated = await coord.admit(now_ms=0, **kw)
    assert not saturated.admitted
    assert saturated.reason == "concurrency_saturated"
    assert saturated.http_status == 503
    assert saturated.retry_after_seconds > 0
    # Releasing a holder frees exactly one slot.
    assert await coord.release(resource="run", identity="t:acme", lease_id=a.lease_id)
    assert (await coord.admit(now_ms=0, **kw)).admitted


async def test_expired_lease_frees_slot(coord: QuotaCoordinator) -> None:
    kw = dict(resource="run", identity="t:beta", capacity=1000, refill_per_sec=1000.0,
              max_concurrency=1, lease_ms=1000)
    first = await coord.admit(now_ms=0, **kw)
    assert first.admitted
    assert not (await coord.admit(now_ms=500, **kw)).admitted  # still held
    # After the lease expires the purge reclaims the slot.
    assert (await coord.admit(now_ms=2000, **kw)).admitted


async def test_scopes_are_isolated(coord: QuotaCoordinator) -> None:
    kw = dict(resource="chat", capacity=1, refill_per_sec=0.001,
              max_concurrency=100, lease_ms=60_000)
    assert (await coord.admit(now_ms=0, identity="u:a", **kw)).admitted
    assert not (await coord.admit(now_ms=0, identity="u:a", **kw)).admitted
    # A different identity has its own bucket.
    assert (await coord.admit(now_ms=0, identity="u:b", **kw)).admitted


async def test_release_is_idempotent(coord: QuotaCoordinator) -> None:
    d = await coord.admit(now_ms=0, resource="run", identity="t:x", capacity=1000,
                          refill_per_sec=1000.0, max_concurrency=1, lease_ms=60_000)
    assert d.lease_id
    assert await coord.release(resource="run", identity="t:x", lease_id=d.lease_id)
    # Second release is a no-op, not an error.
    assert not await coord.release(resource="run", identity="t:x", lease_id=d.lease_id)
    assert not await coord.release(resource="run", identity="t:x", lease_id="never")


async def test_concurrent_admits_respect_max_concurrency(
    coord: QuotaCoordinator,
) -> None:
    kw = dict(resource="run", identity="t:race", capacity=1000, refill_per_sec=1000.0,
              max_concurrency=3, lease_ms=60_000)
    results = await asyncio.gather(
        *(coord.admit(now_ms=0, **kw) for _ in range(10))
    )
    admitted = [r for r in results if r.admitted]
    assert len(admitted) == 3  # atomic Lua: never over-admits
    assert len({r.lease_id for r in admitted}) == 3
