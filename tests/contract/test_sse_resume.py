"""T059 [US2] contract for SSE event replay over Redis Streams.

A run's progress events are appended to a bounded, TTL'd Redis Stream so a client
that reconnects can resume from its ``Last-Event-ID`` instead of losing the tail.
The stream is bounded (oldest entries are trimmed) and expiring, so a client that
fell too far behind is told there is a *gap* and should refetch a snapshot from
the PostgreSQL authority rather than silently missing milestones.

Runs against a real Redis and skips cleanly when none is reachable; nothing here
fakes the stream.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

redis_asyncio = pytest.importorskip("redis.asyncio")

from backend.app.sse.stream import RunEventStream  # noqa: E402

REDIS_URL = os.environ.get("POLICYFLOW_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
RUN = "run-1"


@pytest_asyncio.fixture
async def stream() -> AsyncIterator[RunEventStream]:
    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    prefix = f"test:sse:{uuid.uuid4().hex}"
    try:
        yield RunEventStream(client, prefix=prefix, max_events=1000, ttl_seconds=3600)
    finally:
        keys = [k async for k in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_publish_and_replay_all(stream: RunEventStream) -> None:
    await stream.publish(RUN, "run.started", {"n": 1})
    await stream.publish(RUN, "run.progress", {"n": 2})
    await stream.publish(RUN, "run.succeeded", {"n": 3})
    result = await stream.replay(RUN, after_id="0")
    assert not result.gap
    assert [e.event_type for e in result.events] == [
        "run.started", "run.progress", "run.succeeded"
    ]
    assert [e.data["n"] for e in result.events] == [1, 2, 3]


async def test_replay_resumes_after_last_id(stream: RunEventStream) -> None:
    first = await stream.publish(RUN, "run.started", {"n": 1})
    await stream.publish(RUN, "run.progress", {"n": 2})
    await stream.publish(RUN, "run.succeeded", {"n": 3})
    result = await stream.replay(RUN, after_id=first)
    assert not result.gap
    assert [e.data["n"] for e in result.events] == [2, 3]  # excludes the seen id


async def test_bounded_stream_trims_oldest(stream: RunEventStream) -> None:
    bounded = RunEventStream(stream._redis, prefix=stream._prefix, max_events=3,
                             ttl_seconds=3600)
    for i in range(5):
        await bounded.publish(RUN, "run.progress", {"n": i})
    result = await bounded.replay(RUN, after_id="0")
    assert [e.data["n"] for e in result.events] == [2, 3, 4]  # only the last 3


async def test_resume_from_trimmed_id_reports_gap(stream: RunEventStream) -> None:
    bounded = RunEventStream(stream._redis, prefix=stream._prefix, max_events=3,
                             ttl_seconds=3600)
    first = await bounded.publish(RUN, "run.progress", {"n": 0})
    for i in range(1, 5):
        await bounded.publish(RUN, "run.progress", {"n": i})
    # `first` has been trimmed; resuming from it must flag a gap.
    result = await bounded.replay(RUN, after_id=first)
    assert result.gap
    assert [e.data["n"] for e in result.events] == [2, 3, 4]


async def test_ttl_is_set_on_the_stream(stream: RunEventStream) -> None:
    await stream.publish(RUN, "run.started", {})
    ttl = await stream.ttl_seconds_remaining(RUN)
    assert ttl is not None and ttl > 0


async def test_latest_id_tracks_last_publish(stream: RunEventStream) -> None:
    await stream.publish(RUN, "run.started", {})
    last = await stream.publish(RUN, "run.succeeded", {})
    assert await stream.latest_id(RUN) == last
