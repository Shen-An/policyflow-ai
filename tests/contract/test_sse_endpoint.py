"""T068 [US2] SSE HTTP surface: replay-then-live composition, verified honestly.

Two layers are proven here, at the level each can actually be proven on this
host:

* **The composition generator** (:func:`sse_event_source`) is unit-tested with a
  fake stream and a real :class:`BoundedEventChannel`: replay ordering, the
  ``snapshot_required`` control frame on a trimmed resume point, live pass-through
  with heartbeats, and a clean return when the channel closes (the resource
  release that lets a disconnected connection unwind). No infra needed -- this
  always runs.
* **The HTTP endpoint** (``GET /api/v2/runs/{run_id}/events``) is exercised
  end-to-end against a **real Redis**, skipped cleanly when none is reachable.
  It asserts tenant-scoped authorization (cross-tenant is ``404`` before any
  stream opens), replay of the retained backlog as SSE frames with ids, and
  ``Last-Event-ID`` resume. Nothing here fakes the stream.

What is intentionally NOT claimed: the live tail held open for worker-produced
events. That needs a producer fanning run events into per-connection channels,
which belongs with the Celery consumers (T064) and is gated on the broker. The
endpoint runs in replay/catch-up mode, so it returns once the backlog drains.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
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
from backend.app.sse.channel import BoundedEventChannel, ChannelEvent
from backend.app.sse.endpoint import (
    CONTROL_HEARTBEAT,
    CONTROL_SNAPSHOT_REQUIRED,
    sse_event_source,
)
from backend.app.sse.stream import ReplayResult, StreamEvent

# --------------------------------------------------------------------------- #
# Part A: generator-level composition (no infra, always runs).
# --------------------------------------------------------------------------- #


class _FakeStream:
    """A stand-in for RunEventStream returning a preset replay result."""

    def __init__(self, result: ReplayResult) -> None:
        self._result = result
        self.seen_after: str | None = None

    async def replay(self, run_id: str, after_id: str = "0") -> ReplayResult:
        self.seen_after = after_id
        return self._result


async def _drain(generator: AsyncIterator[dict]) -> list[dict]:
    return [frame async for frame in generator]


async def test_replay_only_emits_events_then_returns() -> None:
    stream = _FakeStream(
        ReplayResult(
            events=[
                StreamEvent(id="1-0", event_type="run.started", data={"n": 1}),
                StreamEvent(id="2-0", event_type="run.succeeded", data={"n": 2}),
            ],
            gap=False,
        )
    )
    frames = await _drain(
        sse_event_source(stream=stream, run_id="r", last_event_id="0", channel=None)
    )
    assert [f["event"] for f in frames] == ["run.started", "run.succeeded"]
    assert [f["id"] for f in frames] == ["1-0", "2-0"]
    # last_event_id "0" means "from the beginning".
    assert stream.seen_after == "0"


async def test_gap_emits_snapshot_required_before_events() -> None:
    stream = _FakeStream(
        ReplayResult(
            events=[StreamEvent(id="9-0", event_type="run.progress", data={"n": 9})],
            gap=True,
        )
    )
    frames = await _drain(
        sse_event_source(stream=stream, run_id="r", last_event_id="3-0", channel=None)
    )
    assert frames[0]["event"] == CONTROL_SNAPSHOT_REQUIRED
    assert "run_id" in frames[0]["data"]
    assert frames[1]["event"] == "run.progress"
    assert stream.seen_after == "3-0"


async def test_gap_with_durable_snapshot_replays_pg_milestones() -> None:
    """On a gap, a durable snapshot supersedes the partial Redis backlog.

    When Redis was trimmed/flushed the retained Redis events are an incomplete
    tail; the authoritative history is the PostgreSQL durable snapshot. The
    generator must emit ``snapshot_required`` and then the durable milestones,
    NOT the partial Redis events, so no milestone is silently skipped.
    """
    stream = _FakeStream(
        ReplayResult(
            events=[StreamEvent(id="9-0", event_type="run.progress", data={"n": 9})],
            gap=True,
        )
    )

    async def _snapshot() -> list[StreamEvent]:
        return [
            StreamEvent(id="d1", event_type="run.created", data={"sequence": 1}),
            StreamEvent(id="d2", event_type="run.finalized", data={"sequence": 2}),
        ]

    frames = await _drain(
        sse_event_source(
            stream=stream, run_id="r", last_event_id="3-0", channel=None, snapshot=_snapshot
        )
    )
    assert frames[0]["event"] == CONTROL_SNAPSHOT_REQUIRED
    assert [f["event"] for f in frames[1:]] == ["run.created", "run.finalized"]
    assert [f["id"] for f in frames[1:]] == ["d1", "d2"]
    # The partial Redis tail is NOT double-delivered when the snapshot is authoritative.
    assert "run.progress" not in [f["event"] for f in frames]


async def test_gap_without_snapshot_falls_back_to_retained_redis() -> None:
    """With no durable source wired, gap behaviour is unchanged (retained events)."""
    stream = _FakeStream(
        ReplayResult(
            events=[StreamEvent(id="9-0", event_type="run.progress", data={"n": 9})],
            gap=True,
        )
    )
    frames = await _drain(
        sse_event_source(stream=stream, run_id="r", last_event_id="3-0", channel=None)
    )
    assert frames[0]["event"] == CONTROL_SNAPSHOT_REQUIRED
    assert frames[1]["event"] == "run.progress"


async def test_none_last_event_id_replays_from_start() -> None:
    stream = _FakeStream(ReplayResult(events=[], gap=False))
    await _drain(
        sse_event_source(stream=stream, run_id="r", last_event_id=None, channel=None)
    )
    assert stream.seen_after == "0"


async def test_live_channel_streams_then_closes_cleanly() -> None:
    stream = _FakeStream(ReplayResult(events=[], gap=False))
    channel = BoundedEventChannel(capacity=8, heartbeat_seconds=5.0)
    frames: list[dict] = []

    async def consume() -> None:
        async for frame in sse_event_source(
            stream=stream, run_id="r", channel=channel
        ):
            frames.append(frame)

    task = asyncio.create_task(consume())
    await channel.publish(
        ChannelEvent("run.progress", {"n": 1}, id="1-0"), timeout=1.0
    )
    await channel.publish(
        ChannelEvent("run.succeeded", {"n": 2}, id="2-0"), timeout=1.0
    )
    await asyncio.sleep(0.05)
    await channel.aclose()
    await asyncio.wait_for(task, timeout=2.0)

    assert [f["event"] for f in frames] == ["run.progress", "run.succeeded"]
    assert [f["id"] for f in frames] == ["1-0", "2-0"]


async def test_idle_channel_emits_heartbeat_then_closes() -> None:
    stream = _FakeStream(ReplayResult(events=[], gap=False))
    channel = BoundedEventChannel(capacity=4, heartbeat_seconds=0.05)
    frames: list[dict] = []

    async def consume() -> None:
        async for frame in sse_event_source(
            stream=stream, run_id="r", channel=channel
        ):
            frames.append(frame)
            if frame["event"] == CONTROL_HEARTBEAT:
                await channel.aclose()

    await asyncio.wait_for(consume(), timeout=2.0)
    assert frames and frames[0]["event"] == CONTROL_HEARTBEAT


# --------------------------------------------------------------------------- #
# Part B: HTTP endpoint against a real Redis (skipped when unreachable).
# --------------------------------------------------------------------------- #

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"
USER_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROLE_ALPHA = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ROLE_BETA = "cccccccc-cccc-cccc-cccc-ccccccccdddd"
GRANT_ALPHA = "dddddddd-dddd-dddd-dddd-dddddddddddd"
GRANT_BETA = "dddddddd-dddd-dddd-dddd-ddddddddeeee"

SECRET = "t068-secret"
GOOD_KEY = "idem-key-0123456789"  # 19 chars, within 16..128
REDIS_URL = os.environ.get("POLICYFLOW_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


async def _create_and_seed(url: str) -> None:
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        for tenant_id, code in ((TENANT_ALPHA, "alpha"), (TENANT_BETA, "beta")):
            session.add(
                Tenant(id=tenant_id, code=code, name=code.title(), status="active")
            )
        await session.flush()
        for user_id, tenant_id, name in (
            (USER_ALPHA, TENANT_ALPHA, "alpha-member"),
            (USER_BETA, TENANT_BETA, "beta-member"),
        ):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    username=name,
                    email=f"{name}@example.com",
                    password_hash="not-used-by-these-tests",
                    display_name=name,
                    status="active",
                )
            )
        await session.flush()
        for role_id, tenant_id in ((ROLE_ALPHA, TENANT_ALPHA), (ROLE_BETA, TENANT_BETA)):
            session.add(
                Role(
                    id=role_id,
                    tenant_id=tenant_id,
                    code="reader",
                    name="Reader",
                    actions=["read"],
                )
            )
        await session.flush()
        for grant_id, tenant_id, user_id, role_id in (
            (GRANT_ALPHA, TENANT_ALPHA, USER_ALPHA, ROLE_ALPHA),
            (GRANT_BETA, TENANT_BETA, USER_BETA, ROLE_BETA),
        ):
            session.add(
                UserRoleGrant(
                    id=grant_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role_id=role_id,
                    scope="tenant",
                )
            )
        await session.flush()
        await session.commit()
    await engine.dispose()


@pytest.fixture()
def redis_prefix() -> Iterator[str]:
    """A real-Redis fixture: unique prefix, cleaned up; skip if unreachable."""
    redis_asyncio = pytest.importorskip("redis.asyncio")
    prefix = f"test:sse:{uuid.uuid4().hex}"

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
def client(tmp_path: Path, redis_prefix: str) -> Iterator[TestClient]:
    import redis.asyncio as redis_asyncio

    from backend.app.sse.stream import RunEventStream

    db_file = (tmp_path / "runs.db").as_posix()
    url = f"sqlite:///{db_file}"
    asyncio.run(_create_and_seed(url))
    settings = Settings(
        DATABASE_URL=url,
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t068-password",
        _env_file=None,
    )
    app = create_app(settings)
    # Inject a RunEventStream over the real Redis under a unique prefix so the
    # endpoint reads the exact stream this test publishes to (no faking).
    app.state.run_event_stream = RunEventStream(
        redis_asyncio.from_url(REDIS_URL),
        prefix=redis_prefix,
        max_events=1000,
        ttl_seconds=3600,
    )
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client


def _headers(tenant_id: str, subject: str) -> dict[str, str]:
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR="logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t068-password",
        _env_file=None,
    )
    token = create_access_token(subject, settings, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _alpha() -> dict[str, str]:
    return _headers(TENANT_ALPHA, USER_ALPHA)


def _parse_sse(text: str) -> list[dict[str, str]]:
    """Parse the SSE wire format into a list of {field: value} blocks."""
    events: list[dict[str, str]] = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        if not block.strip():
            continue
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if not line or line.startswith(":") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            fields[key] = fields.get(key, "") + value
        if fields:
            events.append(fields)
    return events


async def _publish(prefix: str, run_id: str, events: list[tuple[str, dict]]) -> list[str]:
    import redis.asyncio as redis_asyncio

    from backend.app.sse.stream import RunEventStream

    client = redis_asyncio.from_url(REDIS_URL)
    try:
        stream = RunEventStream(client, prefix=prefix, max_events=1000, ttl_seconds=3600)
        return [await stream.publish(run_id, etype, data) for etype, data in events]
    finally:
        await client.aclose()


def _create_run(client: TestClient) -> str:
    resp = client.post(
        "/api/v2/runs",
        headers={**_alpha(), "Idempotency-Key": GOOD_KEY},
        json={"kind": "kb_reindex", "payload": {"kb": "hr"}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


def test_events_replays_backlog_as_sse_frames(client: TestClient) -> None:
    run_id = _create_run(client)
    prefix = client.app.state.run_event_stream._prefix
    asyncio.run(
        _publish(
            prefix,
            run_id,
            [
                ("run.started", {"n": 1}),
                ("run.progress", {"n": 2}),
                ("run.succeeded", {"n": 3}),
            ],
        )
    )
    resp = client.get(f"/api/v2/runs/{run_id}/events", headers=_alpha())
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.text)
    assert [f["event"] for f in frames] == [
        "run.started",
        "run.progress",
        "run.succeeded",
    ]
    assert all("id" in f for f in frames)


def test_events_resume_from_last_event_id(client: TestClient) -> None:
    run_id = _create_run(client)
    prefix = client.app.state.run_event_stream._prefix
    ids = asyncio.run(
        _publish(
            prefix,
            run_id,
            [
                ("run.started", {"n": 1}),
                ("run.progress", {"n": 2}),
                ("run.succeeded", {"n": 3}),
            ],
        )
    )
    resp = client.get(
        f"/api/v2/runs/{run_id}/events",
        headers={**_alpha(), "Last-Event-ID": ids[0]},
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.text)
    # Excludes the already-seen first event.
    assert [f["event"] for f in frames] == ["run.progress", "run.succeeded"]


def test_events_cross_tenant_is_not_found(client: TestClient) -> None:
    run_id = _create_run(client)
    resp = client.get(
        f"/api/v2/runs/{run_id}/events",
        headers=_headers(TENANT_BETA, USER_BETA),
    )
    assert resp.status_code == 404, resp.text


def test_events_unknown_run_is_not_found(client: TestClient) -> None:
    resp = client.get(
        "/api/v2/runs/does-not-exist/events", headers=_alpha()
    )
    assert resp.status_code == 404, resp.text


def test_events_endpoint_moves_sse_gauge_and_times_cleanup(client: TestClient) -> None:
    """T071 wiring: an events connection opens/closes the gauge and times teardown."""
    pytest.importorskip("opentelemetry.sdk.metrics")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from backend.app.observability import telemetry as tm

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=provider.get_meter("test"))
    try:
        run_id = _create_run(client)
        prefix = client.app.state.run_event_stream._prefix
        # A finished run's backlog (ends terminal) so the endpoint replays and
        # closes; the gauge +1/-1 and cleanup timing happen on that open/close.
        asyncio.run(_publish(prefix, run_id, [("run.started", {"n": 1}), ("run.succeeded", {})]))
        resp = client.get(f"/api/v2/runs/{run_id}/events", headers=_alpha())
        assert resp.status_code == 200, resp.text

        data = reader.get_metrics_data()
        seen: dict[str, list] = {}
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    seen.setdefault(metric.name, []).extend(metric.data.data_points)
        # Gauge touched (net 0 after open+close, but the point exists).
        assert tm.METRIC_SSE_ACTIVE in seen
        # Cleanup timed exactly once for scope "sse".
        cleanup = seen.get(tm.METRIC_CLEANUP_DURATION, [])
        sse_points = [p for p in cleanup if p.attributes.get("scope") == "sse"]
        assert sse_points and sse_points[0].count >= 1
    finally:
        tm.reset_telemetry()
