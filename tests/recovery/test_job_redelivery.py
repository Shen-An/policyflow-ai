"""T057 [US2] duplicate delivery causes no duplicate state change or side effect.

A quorum queue with manual late ack *will* redeliver: a message whose worker
crashed after acting but before acking comes back. The system must absorb that.
Two mechanisms make it safe, and both are proven here on SQLite: terminal-state
transitions are no-ops (a redelivered finish does not re-finish), and outbox
publication is idempotent at the database via the unique
(aggregate_type, aggregate_id, aggregate_version, event_type) constraint (a
redelivered emit cannot produce a second event, hence no duplicate side effect).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.db.models import OutboxEvent, utc_now
from backend.app.db.session import build_async_engine
from backend.app.jobs.service import AGGREGATE_TYPE

# TENANT matches the tenant seeded by the `jobs` fixture in conftest.py.
TENANT = "11111111-1111-1111-1111-111111111111"
WORKER = "worker-a"


async def test_redelivered_enqueue_same_payload_is_idempotent(jobs) -> None:
    svc = jobs.fresh_service()
    a = await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="d"
    )
    b = await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="d"
    )
    assert a.id == b.id
    events = await svc.list_outbox(aggregate_id=a.id)
    assert [e.event_type for e in events] == ["job.enqueued"]  # not duplicated


async def test_redelivered_completion_does_not_re_transition(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    first = await svc.complete(job_id=job.id, worker_id=WORKER, result_ref="ok")
    # Redelivery: the same finish message is processed again.
    second = await svc.complete(job_id=job.id, worker_id=WORKER, result_ref="ok")
    assert first.version == second.version  # no second transition
    events = [e.event_type for e in await svc.list_outbox(aggregate_id=job.id)]
    assert events.count("job.succeeded") == 1  # exactly one side-effect event


async def test_redelivery_after_terminal_does_not_reopen(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=1
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    await svc.fail(job_id=job.id, worker_id=WORKER, error_code="BUG", recoverable=False)
    # A redelivered message must not make a terminal job leasable again.
    assert await svc.lease(worker_id=WORKER, lease_seconds=60) is None
    refreshed = await svc.get(job.id)
    assert refreshed is not None and refreshed.state == "terminal_failed"


async def test_redelivered_cancel_finalize_is_noop(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    await svc.request_cancel(job_id=job.id)
    first = await svc.finalize_cancel(job_id=job.id)
    second = await svc.finalize_cancel(job_id=job.id)  # redelivered
    assert first.version == second.version
    events = [e.event_type for e in await svc.list_outbox(aggregate_id=job.id)]
    assert events.count("job.cancelled") == 1


async def test_duplicate_outbox_publication_is_rejected_at_the_database(
    jobs,
) -> None:
    # The unique aggregate-version-event constraint is the last line of defense:
    # even a racing double-emit for the same transition inserts only one row.
    svc = jobs.fresh_service()
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    engine = build_async_engine(jobs.url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                OutboxEvent(
                    tenant_id=TENANT,
                    aggregate_type=AGGREGATE_TYPE,
                    aggregate_id=job.id,
                    aggregate_version=1,  # collides with the job.enqueued event
                    event_type="job.enqueued",
                    payload={},
                    available_at=utc_now(),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()
