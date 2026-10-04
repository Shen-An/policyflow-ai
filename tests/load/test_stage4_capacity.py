"""T072 [US2] Stage-4 capacity / overload / resource-cleanup evidence.

The repo's legacy Locust profiles (``tests/load/locustfile.py``) drive the *v1*
``/api/chat`` + ``/api/chat/stream`` surface, which needs a live LLM and is not the
Stage-4 run/quota/SSE surface this phase built. Rather than fake an LLM or test the
wrong surface, this module exercises the **actual** Stage-4 surface at concurrency,
in-process over httpx's ASGI transport (no OS-socket ceiling, no LLM), against a
**real Redis** coordinator and stream (skips cleanly when Redis is unreachable --
never faked). It produces the T072 evidence the goal asks for:

* **Overload -> controlled shed (429/503 + Retry-After, no 5xx).** A concurrent
  burst of ``POST /api/v2/runs`` far exceeding the quota admits at most the budget
  and refuses the rest with ``503``/``429`` + ``Retry-After`` -- never a 5xx. This
  is "过载明确返回 429/503 + Retry-After" on the cross-instance-atomic Redis path.
* **Concurrent SSE + resource cleanup.** Many concurrent ``GET
  /runs/{id}/events`` replay-and-close connections drive the live-SSE gauge up and
  back to **0** after they unwind (every teardown timed into the ``sse`` cleanup
  histogram) -- "disconnected resources freed", observable, not leaked. (The
  endpoint is replay-then-close; the live-tail producer that would hold a
  connection open is gated on T064's fan-out and is honestly out of scope here.)
* **Redis short outage -> fail closed.** With the admission backend unreachable the
  gate returns ``503`` + ``Retry-After`` (``ADMISSION_UNAVAILABLE``), not a 500, and
  recovers to ``201`` once Redis is back -- an overload gate that cannot verify
  capacity sheds load rather than waving traffic through.

Evidence summaries are written to ``artifacts/recovery/stage4/``.
"""

from __future__ import annotations

import asyncio
import json
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
from backend.app.sse.stream import RunEventStream

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
USER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GRANT = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SECRET = "t072-capacity-secret"
REDIS_URL = os.environ.get("POLICYFLOW_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
DEAD_REDIS_URL = "redis://127.0.0.1:6399/0"  # nothing listens here -> outage
ARTIFACT_DIR = Path("artifacts/recovery/stage4")


async def _seed(url: str) -> None:
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT, code="alpha", name="Alpha", status="active"))
        await session.flush()
        session.add(User(
            id=USER, tenant_id=TENANT, username="member", email="m@example.com",
            password_hash="x", display_name="member", status="active",
        ))
        await session.flush()
        session.add(Role(id=ROLE, tenant_id=TENANT, code="reader", name="Reader",
                         actions=["read"]))
        await session.flush()
        session.add(UserRoleGrant(id=GRANT, tenant_id=TENANT, user_id=USER,
                                  role_id=ROLE, scope="tenant"))
        await session.commit()
    await engine.dispose()


def _write_artifact(name: str, payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / name).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _headers(key: str) -> dict[str, str]:
    settings = Settings(DATABASE_URL="sqlite://", LOG_DIR="logs", SECRET_KEY=SECRET,
                        _env_file=None)
    token = create_access_token(USER, settings, tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


@pytest.fixture()
def redis_url() -> Iterator[str]:
    redis_asyncio = pytest.importorskip("redis.asyncio")

    async def _ping() -> None:
        client = redis_asyncio.from_url(REDIS_URL)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(_ping())
    except Exception:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    prefix = f"test:cap:{uuid.uuid4().hex}"
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
def app(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'cap.db').as_posix()}"
    asyncio.run(_seed(url))
    settings = Settings(DATABASE_URL=url, LOG_DIR=tmp_path / "logs", SECRET_KEY=SECRET,
                        REDIS_URL=REDIS_URL, _env_file=None)
    # Built without triggering lifespan (ASGITransport): schema is seeded above and
    # telemetry is configured per-test, so the lifespan's configure_telemetry does
    # not clobber the in-memory meter the SSE test reads.
    return create_app(settings)


async def _aclient(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://cap.test"
    )


def _coordinator(url: str, prefix: str) -> QuotaCoordinator:
    import redis.asyncio as redis_asyncio

    return QuotaCoordinator(redis_asyncio.from_url(url), prefix=prefix)


async def test_overload_burst_sheds_load_without_5xx(app, redis_url: str) -> None:
    budget = 8
    app.state.run_admission = CoordinatorAdmission(
        _coordinator(REDIS_URL, redis_url),
        limits_provider=lambda _r, _i: QuotaLimits(
            capacity=1000, refill_per_sec=1000.0, max_concurrency=budget, lease_ms=60_000
        ),
    )
    burst = 100
    async with await _aclient(app) as client:
        async def _submit(i: int) -> httpx.Response:
            return await client.post(
                "/api/v2/runs", headers=_headers(f"burst-key-{i:08d}"),
                json={"kind": "kb_reindex", "payload": {"n": i}},
            )

        responses = await asyncio.gather(*(_submit(i) for i in range(burst)))

    codes = [r.status_code for r in responses]
    admitted = sum(c == 201 for c in codes)
    rejected = [r for r in responses if r.status_code in (429, 503)]
    server_errors = [c for c in codes if c >= 500 and c != 503]
    # Overload is shed in a controlled way: at most the budget admitted, every
    # rejection carries a positive Retry-After, and nothing collapses into a 5xx
    # other than the deliberate 503 shed.
    assert admitted <= budget, f"admitted {admitted} exceeded budget {budget}"
    assert admitted + len(rejected) == burst
    assert server_errors == [], f"unexpected server errors: {server_errors}"
    assert all(int(r.headers["Retry-After"]) >= 1 for r in rejected)
    assert all(
        r.json()["error"]["code"] in {"RATE_LIMITED", "CONCURRENCY_SATURATED"}
        for r in rejected
    )
    _write_artifact("capacity-saturation.json", {
        "scenario": "POST /api/v2/runs concurrent burst vs quota (real Redis)",
        "burst": burst, "budget_max_concurrency": budget,
        "admitted_201": admitted, "shed_429": sum(c == 429 for c in codes),
        "shed_503": sum(c == 503 for c in codes), "server_errors_5xx": len(server_errors),
        "all_rejections_have_retry_after": True,
    })


async def test_concurrent_sse_replay_frees_resources(app, redis_url: str) -> None:
    # Generous admission so the setup run is accepted.
    app.state.run_admission = CoordinatorAdmission(
        _coordinator(REDIS_URL, redis_url),
        limits_provider=lambda _r, _i: QuotaLimits(
            capacity=1000, refill_per_sec=1000.0, max_concurrency=1000, lease_ms=60_000
        ),
    )
    import redis.asyncio as redis_asyncio
    stream = RunEventStream(redis_asyncio.from_url(REDIS_URL), prefix=f"{redis_url}:sse")
    app.state.run_event_stream = stream

    # In-memory meter so we can read the live-SSE gauge and the cleanup histogram.
    pytest.importorskip("opentelemetry.sdk.metrics")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=MeterProvider(metric_readers=[reader]).get_meter("t072"))
    try:
        async with await _aclient(app) as client:
            created = await client.post(
                "/api/v2/runs", headers=_headers("sse-setup-key-0001"),
                json={"kind": "kb_reindex", "payload": {}},
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["run_id"]
            # Seed a finished run's backlog (ends terminal) so each connection
            # replays and closes -- this measures concurrent replay cleanup. The
            # live-tail hold-open path is covered by test_sse_live_tail.py.
            for i in range(20):
                await stream.publish(run_id, "progress", {"step": i})
            await stream.publish(run_id, "run.succeeded", {})

            # Concurrency here is deliberately within the async pool: each in-flight
            # SSE request pins a DB connection (via the principal dependency) for the
            # request's lifetime, so concurrent SSE is bounded by DATABASE_POOL_SIZE/
            # MAX_OVERFLOW. SQLite ignores those (default ~15); production is a tuned
            # PG pool. We prove the cleanup *invariant* under genuine concurrency; the
            # pool bound itself is recorded as an honest capacity finding.
            n = 10

            async def _connect() -> int:
                resp = await client.get(f"/api/v2/runs/{run_id}/events", headers=_headers("x" * 20))
                return resp.status_code

            statuses = await asyncio.gather(*(_connect() for _ in range(n)))

        assert all(s == 200 for s in statuses), f"non-200 SSE replays: {set(statuses)}"
        gauge = _gauge_value(reader, tm.METRIC_SSE_ACTIVE)
        cleanup = _hist_count(reader, tm.METRIC_CLEANUP_DURATION, scope="sse")
        # Every connection opened and unwound: the gauge returns to 0 (no leak) and
        # every teardown was timed into the sse cleanup histogram.
        assert gauge == 0, f"live-SSE gauge did not return to 0: {gauge}"
        assert cleanup >= n, f"expected >={n} sse cleanup timings, got {cleanup}"
        _write_artifact("capacity-sse-cleanup.json", {
            "scenario": "concurrent GET /runs/{id}/events replay-and-close (real Redis)",
            "concurrent_connections": n, "all_200": True,
            "live_sse_gauge_after": gauge, "sse_cleanup_timings": cleanup,
            "note": (
                "replay-then-close endpoint; live-tail hold-open is T064-gated. "
                "Each in-flight SSE pins a DB connection (principal dependency) for "
                "the request lifetime, so concurrent SSE is bounded by the async DB "
                "pool (DATABASE_POOL_SIZE/MAX_OVERFLOW); SQLite ignores those (~15), "
                "production is a tuned PG pool. Invariant proven under concurrency: "
                "every opened connection unwinds, the live-SSE gauge returns to 0 "
                "(no leak), and every teardown is timed into the sse cleanup histogram."
            ),
        })
    finally:
        tm.reset_telemetry()


async def test_redis_outage_fails_closed_then_recovers(app, redis_url: str) -> None:
    # Backend unreachable: the gate must fail closed with 503 (not 500).
    app.state.run_admission = CoordinatorAdmission(
        _coordinator(DEAD_REDIS_URL, "test:dead"),
        limits_provider=lambda _r, _i: QuotaLimits(
            capacity=10, refill_per_sec=1.0, max_concurrency=10, lease_ms=60_000
        ),
        fail_closed_retry_seconds=3.0,
    )
    async with await _aclient(app) as client:
        down = await client.post(
            "/api/v2/runs", headers=_headers("outage-key-00000001"),
            json={"kind": "kb_reindex", "payload": {}},
        )
        assert down.status_code == 503, down.text
        assert int(down.headers["Retry-After"]) >= 1
        assert down.json()["error"]["code"] == "ADMISSION_UNAVAILABLE"

        # Redis "returns": rebind to the live coordinator and the gate recovers.
        app.state.run_admission = CoordinatorAdmission(
            _coordinator(REDIS_URL, redis_url),
            limits_provider=lambda _r, _i: QuotaLimits(
                capacity=1000, refill_per_sec=1000.0, max_concurrency=1000, lease_ms=60_000
            ),
        )
        up = await client.post(
            "/api/v2/runs", headers=_headers("recovered-key-0001"),
            json={"kind": "kb_reindex", "payload": {}},
        )
        assert up.status_code == 201, up.text
    _write_artifact("redis-outage-drill.json", {
        "scenario": "admission backend outage then recovery",
        "during_outage_status": 503, "error_code": "ADMISSION_UNAVAILABLE",
        "after_recovery_status": 201, "behavior": "fail closed (shed load), then recover",
    })


def _gauge_value(reader, name: str) -> float:
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


def _hist_count(reader, name: str, *, scope: str) -> int:
    data = reader.get_metrics_data()
    count = 0
    if data is None:
        return count
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    for point in metric.data.data_points:
                        if point.attributes.get("scope") == scope:
                            count += point.count
    return count
