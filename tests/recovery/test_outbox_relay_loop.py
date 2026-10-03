"""T065/T070 [US2] background outbox relay loop (broker-free).

The live full-path round-trip (enqueue -> relay -> broker -> worker) lives in
``tests/integration/test_consumer_live.py``. This pins the loop's behaviour on the
always-available SQLite path with the in-memory ``MockTransport``: the loop drives
``relay_once`` until a committed ``job.enqueued`` outbox row is published, and it
stops promptly when asked.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.db.session import build_async_engine
from backend.app.jobs.publisher import MockTransport, OutboxPublisher
from backend.app.jobs.relay import OutboxRelayLoop

TENANT = "11111111-1111-1111-1111-111111111111"


async def test_relay_loop_delivers_pending_event_then_stops(jobs) -> None:
    producer = jobs.fresh_service()
    job = await producer.enqueue(
        tenant_id=TENANT, kind="recovery_probe", payload={"token": "r"},
        idempotency_key="relay", max_attempts=3,
    )
    engine = build_async_engine(jobs.url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    transport = MockTransport()
    loop = OutboxRelayLoop(
        OutboxPublisher(factory=factory, transport=transport),
        idle_sleep_seconds=0.05,
    )
    loop.start()
    try:
        delivered = False
        for _ in range(60):
            await asyncio.sleep(0.05)
            if any(getattr(e, "aggregate_id", None) == job.id for e in transport.published):
                delivered = True
                break
        assert delivered, "relay loop never published the pending job.enqueued event"
        published = [e for e in transport.published if e.aggregate_id == job.id]
        assert any(e.event_type == "job.enqueued" for e in published)
    finally:
        await loop.stop()
        await engine.dispose()


async def test_relay_loop_stop_is_prompt_and_idempotent(jobs) -> None:
    engine = build_async_engine(jobs.url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    loop = OutboxRelayLoop(
        OutboxPublisher(factory=factory, transport=MockTransport()),
        idle_sleep_seconds=30.0,  # long idle: stop must not wait it out
    )
    loop.start()
    await asyncio.sleep(0.1)
    await asyncio.wait_for(loop.stop(), timeout=5)  # wakes immediately via stop event
    await loop.stop()  # idempotent: stopping an already-stopped loop is a no-op
    await engine.dispose()
