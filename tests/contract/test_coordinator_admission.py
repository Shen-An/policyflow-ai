"""T069×T066 [US2] production admission binding: ``/api/v2/runs`` over real Redis.

The runs route's :class:`~backend.app.api.routes_runs.RunAdmission` defaults to
allow-all; :class:`~backend.app.quota.admission.CoordinatorAdmission` is the
production binding to the Redis :class:`~backend.app.quota.coordinator.QuotaCoordinator`.
This suite proves that binding end-to-end against a **real Redis** (skipped
cleanly when none is reachable -- never faked): with the adapter wired onto
``app.state.run_admission``, an exhausted token bucket makes ``POST /api/v2/runs``
return ``429`` + ``Retry-After`` and a saturated lease semaphore returns ``503``
+ ``Retry-After``, over the same atomic Lua path a multi-instance deployment
shares. An admitted request still reaches the durable ``queued`` state.

Honest boundary (asserted, not hidden): the adapter holds the concurrency lease
and does not release it -- explicit release on run-terminal is worker-side (T064,
broker-gated), and until then the slot is TTL-reclaimed. That is why the ``503``
test fills the pool with held leases and the next submit is refused.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import Role, Tenant, User, UserRoleGrant
from backend.app.db.session import build_async_engine
from backend.app.main import create_app
from backend.app.quota.admission import CoordinatorAdmission, QuotaLimits
from backend.app.quota.coordinator import QuotaCoordinator

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
USER_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE_ALPHA = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GRANT_ALPHA = "dddddddd-dddd-dddd-dddd-dddddddddddd"

SECRET = "t069x066-secret"
GOOD_KEY = "idem-key-0123456789"  # 19 chars, within 16..128
REDIS_URL = os.environ.get("POLICYFLOW_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

async def _create_and_seed(url: str) -> None:
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT_ALPHA, code="alpha", name="Alpha", status="active"))
        await session.flush()
        session.add(
            User(
                id=USER_ALPHA,
                tenant_id=TENANT_ALPHA,
                username="alpha-member",
                email="alpha-member@example.com",
                password_hash="not-used-by-these-tests",
                display_name="alpha-member",
                status="active",
            )
        )
        await session.flush()
        session.add(
            Role(id=ROLE_ALPHA, tenant_id=TENANT_ALPHA, code="reader", name="Reader",
                 actions=["read"])
        )
        await session.flush()
        session.add(
            UserRoleGrant(id=GRANT_ALPHA, tenant_id=TENANT_ALPHA, user_id=USER_ALPHA,
                          role_id=ROLE_ALPHA, scope="tenant")
        )
        await session.flush()
        await session.commit()
    await engine.dispose()


@pytest.fixture()
def redis_prefix() -> Iterator[str]:
    """Unique real-Redis prefix; skip if unreachable, purge keys on teardown."""
    redis_asyncio = pytest.importorskip("redis.asyncio")
    prefix = f"test:admission:{uuid.uuid4().hex}"

    async def _ping() -> None:
        client = redis_asyncio.from_url(REDIS_URL)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(_ping())
    except Exception:  # noqa: BLE001 - any connect failure gates the suite
        pytest.skip(f"Redis not reachable at {REDIS_URL}")

    yield prefix

    async def _cleanup() -> None:
        client = redis_asyncio.from_url(REDIS_URL)
        try:
            keys = [k async for k in client.scan_iter(match=f"{prefix}:*")]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()

    asyncio.run(_cleanup())


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_file = (tmp_path / "runs.db").as_posix()
    url = f"sqlite:///{db_file}"
    asyncio.run(_create_and_seed(url))
    settings = Settings(
        DATABASE_URL=url,
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t069x066-password",
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        test_client.app = app  # expose for admission injection
        yield test_client


def _wire(test_client: TestClient, prefix: str, limits: QuotaLimits) -> None:
    """Bind the real Redis coordinator onto the app under a unique prefix.

    A fresh ``redis.asyncio`` client is created here and bound to the TestClient
    event loop on first command (inside the request), matching the real-Redis
    HTTP tests already in this package.
    """
    import redis.asyncio as redis_asyncio

    coordinator = QuotaCoordinator(redis_asyncio.from_url(REDIS_URL), prefix=prefix)
    test_client.app.state.run_admission = CoordinatorAdmission(
        coordinator, limits_provider=lambda _resource, _identity: limits
    )


def _headers(tenant_id: str, subject: str) -> dict[str, str]:
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR="logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t069x066-password",
        _env_file=None,
    )
    token = create_access_token(subject, settings, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _alpha(key: str = GOOD_KEY) -> dict[str, str]:
    return {**_headers(TENANT_ALPHA, USER_ALPHA), "Idempotency-Key": key}


def _submit(client: TestClient, key: str):
    return client.post(
        "/api/v2/runs",
        headers=_alpha(key),
        json={"kind": "kb_reindex", "payload": {"kb": "hr"}},
    )


def test_admits_under_budget_and_run_reaches_queued(
    client: TestClient, redis_prefix: str
) -> None:
    _wire(
        client,
        redis_prefix,
        QuotaLimits(capacity=5, refill_per_sec=0.001, max_concurrency=10, lease_ms=60_000),
    )
    resp = _submit(client, "idem-admit-00000001")
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "queued"


def test_rate_limited_submit_returns_429_with_retry_after(
    client: TestClient, redis_prefix: str
) -> None:
    # One token, no meaningful refill within the sub-second test: the second
    # submit finds the bucket empty and is rate-limited by the real coordinator.
    _wire(
        client,
        redis_prefix,
        QuotaLimits(capacity=1, refill_per_sec=1.0, max_concurrency=50, lease_ms=60_000),
    )
    first = _submit(client, "idem-rate-00000001")
    assert first.status_code == 201, first.text

    denied = _submit(client, "idem-rate-00000002")
    assert denied.status_code == 429, denied.text
    assert int(denied.headers["Retry-After"]) >= 1
    assert denied.json()["error"]["code"] == "RATE_LIMITED"


def test_concurrency_saturated_submit_returns_503_with_retry_after(
    client: TestClient, redis_prefix: str
) -> None:
    # One concurrency slot, ample tokens: the first admit holds the only lease
    # (not released -- worker-gated), so the second submit is refused 503.
    _wire(
        client,
        redis_prefix,
        QuotaLimits(capacity=100, refill_per_sec=100.0, max_concurrency=1, lease_ms=30_000),
    )
    first = _submit(client, "idem-conc-00000001")
    assert first.status_code == 201, first.text

    denied = _submit(client, "idem-conc-00000002")
    assert denied.status_code == 503, denied.text
    assert int(denied.headers["Retry-After"]) >= 1
    assert denied.json()["error"]["code"] == "CONCURRENCY_SATURATED"


async def test_adapter_holds_lease_and_maps_saturation() -> None:
    """Adapter-level honesty: an admitted request holds its concurrency lease
    (release is worker-gated), so a second check saturates and maps to 503."""
    redis_asyncio = pytest.importorskip("redis.asyncio")
    redis_client = redis_asyncio.from_url(REDIS_URL)
    try:
        await redis_client.ping()
    except Exception:  # noqa: BLE001 - any connect failure gates the test
        await redis_client.aclose()
        pytest.skip(f"Redis not reachable at {REDIS_URL}")

    prefix = f"test:admission:{uuid.uuid4().hex}"
    coordinator = QuotaCoordinator(redis_client, prefix=prefix)
    adapter = CoordinatorAdmission(
        coordinator,
        limits_provider=lambda _r, _i: QuotaLimits(
            capacity=1, refill_per_sec=0.0, max_concurrency=1, lease_ms=60_000
        ),
    )
    try:
        admitted = await adapter.admit(resource="chat", identity="t:u")
        assert admitted.admitted
        # The lease is still held -- the adapter does not release it here.
        assert await coordinator.active_slots(resource="chat", identity="t:u") == 1

        saturated = await adapter.admit(resource="chat", identity="t:u")
        assert not saturated.admitted
        assert saturated.http_status == 503
        assert saturated.error_code == "CONCURRENCY_SATURATED"
        assert saturated.retry_after_seconds > 0
    finally:
        keys = [k async for k in redis_client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis_client.delete(*keys)
        await redis_client.aclose()
