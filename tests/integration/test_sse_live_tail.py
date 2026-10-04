"""T067/T068/T072 [US2] live-tail SSE: held-open connections receive live events.

Unlike the replay-then-close path, these prove the **live tail**: a connection
stays open, receives events a producer appends *after* it connected (a worker on
any instance, here simulated by publishing to the same cross-instance Redis
stream), and returns on the terminal event so the connection's resources are
released. Needs a real Redis (skips cleanly otherwise).

* single connection: receives started/progress/succeeded live, then the stream
  closes on the terminal event and the live-SSE gauge returns to 0.
* medium concurrency (N held-open connections): every connection receives the
  live events and the terminal, all close, and the gauge returns to 0 -- the
  "disconnected resources freed" invariant under genuine concurrent hold-open.
  (The 1000-scale load run is deferred; concurrent hold-open is bounded by the
  per-connection DB pool pinning in get_principal, documented in the T072 README.)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import Role, Tenant, User, UserRoleGrant
from backend.app.db.session import build_async_engine
from backend.app.main import create_app
from backend.app.observability import telemetry as tm
from backend.app.quota.admission import CoordinatorAdmission, QuotaLimits
from backend.app.quota.coordinator import QuotaCoordinator
from backend.app.sse.run_events import RUN_PROGRESS, RUN_STARTED, RUN_SUCCEEDED
from backend.app.sse.stream import RunEventStream

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
USER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GRANT = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SECRET = "t068-livetail-secret"
REDIS_URL = os.environ.get("POLICYFLOW_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


async def _seed(url: str) -> None:
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT, code="alpha", name="Alpha", status="active"))
        await session.flush()
        session.add(User(id=USER, tenant_id=TENANT, username="m", email="m@x.io",
                         password_hash="x", display_name="m", status="active"))
        await session.flush()
        session.add(Role(id=ROLE, tenant_id=TENANT, code="reader", name="Reader",
                         actions=["read"]))
        await session.flush()
        session.add(UserRoleGrant(id=GRANT, tenant_id=TENANT, user_id=USER,
                                  role_id=ROLE, scope="tenant"))
        await session.commit()
    await engine.dispose()


def _headers(key: str) -> dict[str, str]:
    settings = Settings(DATABASE_URL="sqlite://", LOG_DIR="logs", SECRET_KEY=SECRET, _env_file=None)
    token = create_access_token(USER, settings, tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


@pytest.fixture()
def redis_prefix() -> Iterator[str]:
    redis_asyncio = pytest.importorskip("redis.asyncio")

    async def _ping() -> None:
        c = redis_asyncio.from_url(REDIS_URL)
        try:
            await c.ping()
        finally:
            await c.aclose()

    try:
        asyncio.run(_ping())
    except Exception:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    prefix = f"test:livetail:{uuid.uuid4().hex}"
    yield prefix

    async def _cleanup() -> None:
        c = redis_asyncio.from_url(REDIS_URL)
        try:
            keys = [k async for k in c.scan_iter(match=f"{prefix}:*")]
            if keys:
                await c.delete(*keys)
        finally:
            await c.aclose()

    asyncio.run(_cleanup())


@pytest.fixture()
def app(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'lt.db').as_posix()}"
    asyncio.run(_seed(url))
    settings = Settings(DATABASE_URL=url, LOG_DIR=tmp_path / "logs", SECRET_KEY=SECRET,
                        REDIS_URL=REDIS_URL, SSE_LIVE_TAIL_BLOCK_MS=500, _env_file=None)
    return create_app(settings)


def _stream(prefix: str) -> RunEventStream:
    import redis.asyncio as redis_asyncio

    return RunEventStream(redis_asyncio.from_url(REDIS_URL), prefix=f"{prefix}:sse")


def _wire_admission(app, prefix: str) -> None:
    import redis.asyncio as redis_asyncio

    # Generous admission so the setup run is accepted and stays queued (non-terminal).
    app.state.run_admission = CoordinatorAdmission(
        QuotaCoordinator(redis_asyncio.from_url(REDIS_URL), prefix=f"{prefix}:q"),
        limits_provider=lambda _r, _i: QuotaLimits(
            capacity=10_000, refill_per_sec=10_000.0, max_concurrency=10_000, lease_ms=60_000
        ),
    )


async def _create_run(client: httpx.AsyncClient) -> str:
    created = await client.post("/api/v2/runs", headers=_headers("livetail-setup-0001"),
                                json={"kind": "kb_reindex", "payload": {}})
    assert created.status_code == 201, created.text
    return created.json()["run_id"]


async def _collect_events(client: httpx.AsyncClient, run_id: str, received: list[str]) -> None:
    async with client.stream("GET", f"/api/v2/runs/{run_id}/events",
                             headers=_headers("y" * 20)) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                etype = line.split(":", 1)[1].strip()
                received.append(etype)
                if etype == RUN_SUCCEEDED:  # terminal: the live tail returns, stream closes
                    return


async def test_live_tail_delivers_live_events_and_closes_on_terminal(app, redis_prefix) -> None:
    stream = _stream(redis_prefix)
    app.state.run_event_stream = stream
    _wire_admission(app, redis_prefix)
    pytest.importorskip("opentelemetry.sdk.metrics")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=MeterProvider(metric_readers=[reader]).get_meter("lt"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://lt.test"
        ) as client:
            run_id = await _create_run(client)
            received: list[str] = []

            async def _produce() -> None:
                await asyncio.sleep(0.6)  # let the tail start blocking on XREAD
                await stream.publish(run_id, RUN_STARTED, {})
                await stream.publish(run_id, RUN_PROGRESS, {"pct": 50})
                await stream.publish(run_id, RUN_SUCCEEDED, {})

            await asyncio.wait_for(
                asyncio.gather(_collect_events(client, run_id, received), _produce()),
                timeout=20,
            )

        assert RUN_STARTED in received
        assert RUN_PROGRESS in received
        assert received[-1] == RUN_SUCCEEDED  # terminal closed the stream
        assert _gauge(reader, tm.METRIC_SSE_ACTIVE) == 0  # resources released
    finally:
        tm.reset_telemetry()


async def test_concurrent_held_open_connections_all_receive_and_clean_up(app, redis_prefix) -> None:
    stream = _stream(redis_prefix)
    app.state.run_event_stream = stream
    _wire_admission(app, redis_prefix)
    pytest.importorskip("opentelemetry.sdk.metrics")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=MeterProvider(metric_readers=[reader]).get_meter("lt"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://lt.test"
        ) as client:
            run_id = await _create_run(client)
            n = 10
            buckets: list[list[str]] = [[] for _ in range(n)]

            async def _produce() -> None:
                await asyncio.sleep(1.0)  # let all N tails reach their blocking read
                await stream.publish(run_id, RUN_STARTED, {})
                await stream.publish(run_id, RUN_PROGRESS, {"pct": 100})
                await stream.publish(run_id, RUN_SUCCEEDED, {})

            await asyncio.wait_for(
                asyncio.gather(
                    *(_collect_events(client, run_id, buckets[i]) for i in range(n)),
                    _produce(),
                ),
                timeout=30,
            )

        for i, got in enumerate(buckets):
            assert RUN_STARTED in got, f"conn {i} missed started: {got}"
            assert got[-1] == RUN_SUCCEEDED, f"conn {i} did not close on terminal: {got}"
        assert _gauge(reader, tm.METRIC_SSE_ACTIVE) == 0  # every connection released
    finally:
        tm.reset_telemetry()


async def test_worker_drain_publishes_live_to_a_connected_client(app, redis_prefix) -> None:
    # The definitive end-to-end proof without a broker: an in-process worker drain
    # over the SAME engine + SAME Redis stream publishes run.started/succeeded while
    # a held-open SSE client is tailing, and the client receives them live, then the
    # terminal event closes the stream.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.jobs.runner import JobContext, JobHandlerRegistry, LocalJobRunner
    from backend.app.jobs.service import JobService

    stream = _stream(redis_prefix)
    app.state.run_event_stream = stream
    _wire_admission(app, redis_prefix)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lt.test"
    ) as client:
        run_id = await _create_run(client)

        async def _ok(ctx: JobContext, payload: dict) -> str:
            return "done"

        registry = JobHandlerRegistry()
        registry.register("kb_reindex", _ok)  # the kind _create_run enqueues
        service = JobService(
            factory=async_sessionmaker(app.state.async_engine, expire_on_commit=False)
        )
        runner = LocalJobRunner(
            service=service,
            context=JobContext(engine=app.state.engine, event_stream=stream),
            registry=registry,
            worker_id="e2e-worker",
        )
        received: list[str] = []

        async def _drain() -> None:
            await asyncio.sleep(0.6)  # let the SSE tail start blocking on XREAD
            assert await runner.drain_once() == 1

        await asyncio.wait_for(
            asyncio.gather(_collect_events(client, run_id, received), _drain()),
            timeout=20,
        )

    assert RUN_STARTED in received
    assert received[-1] == RUN_SUCCEEDED  # worker's terminal event closed the stream


def _gauge(reader, name: str) -> float:
    data = reader.get_metrics_data()
    total = 0.0
    if data is None:
        return total
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    for point in metric.data.data_points:
                        total += point.value
    return total
