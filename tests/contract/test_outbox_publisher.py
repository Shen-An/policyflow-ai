"""T065 [US2] transactional-outbox publisher (relay).

The outbox is written in the same transaction as each business change; a
separate relay then publishes pending rows to the broker and marks them
delivered. This is the "at-least-once, dedup-at-consumer" half of the pattern:
the relay may publish a row more than once across a crash, but it never *loses*
one and never double-marks a delivered row. Consumers dedup downstream via the
unique aggregate-version-event constraint (see T057).

Runs on SQLite with an in-memory transport. Per the honesty red lines, the mock
transport tags every envelope ``status="mock"`` so a mocked delivery can never
be mistaken for a real broker ack.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import Tenant
from backend.app.db.session import build_async_engine
from backend.app.jobs.publisher import MockTransport, OutboxPublisher
from backend.app.jobs.service import JobService

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest_asyncio.fixture
async def env(tmp_path) -> AsyncIterator[tuple[JobService, async_sessionmaker]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}"
    engine = build_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT, code="acme", name="Acme"))
        await session.commit()
    try:
        yield JobService(factory=factory), factory
    finally:
        await engine.dispose()


async def test_relays_pending_events_and_marks_delivered(env) -> None:
    svc, factory = env
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    transport = MockTransport()
    publisher = OutboxPublisher(factory=factory, transport=transport)

    delivered = await publisher.relay_once()
    assert delivered == 1
    assert [e.event_type for e in transport.published] == ["job.enqueued"]
    rows = await svc.list_outbox(aggregate_id=job.id)
    assert rows[0].delivery_state == "delivered"
    assert rows[0].delivered_at is not None


async def test_delivered_events_are_not_republished(env) -> None:
    svc, factory = env
    await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    transport = MockTransport()
    publisher = OutboxPublisher(factory=factory, transport=transport)
    assert await publisher.relay_once() == 1
    # A second relay finds nothing pending: idempotent, no duplicate publish.
    assert await publisher.relay_once() == 0
    assert len(transport.published) == 1


async def test_mock_transport_tags_status_mock(env) -> None:
    svc, factory = env
    await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    transport = MockTransport()
    await OutboxPublisher(factory=factory, transport=transport).relay_once()
    assert transport.envelopes  # recorded delivery envelopes
    assert all(env["status"] == "mock" for env in transport.envelopes)


async def test_publish_failure_retries_then_dead_letters(env) -> None:
    svc, factory = env
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    transport = MockTransport(fail_always=True)
    publisher = OutboxPublisher(factory=factory, transport=transport, max_attempts=3)
    # Each relay attempt fails; after the attempt budget the row is dead-lettered.
    for _ in range(3):
        assert await publisher.relay_once() == 0
    rows = await svc.list_outbox(aggregate_id=job.id)
    assert rows[0].delivery_state == "dead_letter"
    assert rows[0].attempts == 3


async def test_transient_failure_then_success(env) -> None:
    svc, factory = env
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    transport = MockTransport(fail_times=1)
    publisher = OutboxPublisher(factory=factory, transport=transport, max_attempts=5)
    assert await publisher.relay_once() == 0  # first attempt fails, row re-pended
    assert await publisher.relay_once() == 1  # recovers
    rows = await svc.list_outbox(aggregate_id=job.id)
    assert rows[0].delivery_state == "delivered"
