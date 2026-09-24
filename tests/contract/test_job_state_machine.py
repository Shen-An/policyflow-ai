"""T055 [US2] contract for the durable job state machine and transactional outbox.

These tests pin the guarantees Phase 4 recovery depends on, and are written to
run on the SQLite development database so the *logic* (compare-and-set
transitions, bounded attempts, idempotent enqueue and idempotent publication)
is provable without a live broker. PostgreSQL-specific concurrency primitives
(``FOR UPDATE SKIP LOCKED``) are exercised separately under ``tests/recovery``
and gate cleanly when no server is running; nothing here fakes a broker.

State machine under test (see ``specs/001-enterprise-agent-refactor/data-model.md``):

    queued/recoverable_failed -> leased -> running -> succeeded
    leased/running            -> recoverable_failed -> (re-queued)
    leased/running            -> terminal_failed
    queued/leased/running     -> cancel_requested -> cancelled
    lease expiry              -> recoverable_failed | terminal_failed
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import DurableJob, OutboxEvent, Tenant, utc_now
from backend.app.db.session import build_async_engine
from backend.app.jobs.service import (
    JobIdempotencyConflict,
    JobService,
    JobStateError,
)

TENANT = "11111111-1111-1111-1111-111111111111"
WORKER = "worker-a"
OTHER_WORKER = "worker-b"


@pytest_asyncio.fixture
async def svc(tmp_path) -> AsyncIterator[JobService]:
    """A JobService bound to a throwaway SQLite database with one tenant."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"
    engine = build_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT, code="acme", name="Acme"))
        await session.commit()
    try:
        yield JobService(factory=factory)
    finally:
        await engine.dispose()


async def _outbox(svc: JobService, job_id: str) -> list[OutboxEvent]:
    events = await svc.list_outbox(aggregate_id=job_id)
    return sorted(events, key=lambda e: e.aggregate_version)


async def test_enqueue_creates_queued_job_and_outbox_row(svc: JobService) -> None:
    job = await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="k1"
    )
    assert job.state == "queued"
    assert job.attempts == 0
    assert job.version == 1
    assert len(job.payload_digest) == 64
    events = await _outbox(svc, job.id)
    assert [e.event_type for e in events] == ["job.enqueued"]
    assert events[0].aggregate_version == 1
    assert events[0].delivery_state == "pending"


async def test_enqueue_is_idempotent_on_same_key_and_digest(svc: JobService) -> None:
    a = await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="dup"
    )
    b = await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="dup"
    )
    assert a.id == b.id
    # No second job, no second outbox row.
    assert len(await _outbox(svc, a.id)) == 1


async def test_enqueue_same_key_different_digest_conflicts(svc: JobService) -> None:
    await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="dup"
    )
    with pytest.raises(JobIdempotencyConflict):
        await svc.enqueue(
            tenant_id=TENANT,
            kind="kb_index",
            payload={"kb": "finance"},
            idempotency_key="dup",
        )


async def test_lease_then_run_then_complete_happy_path(svc: JobService) -> None:
    job = await svc.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={}, idempotency_key="k"
    )
    leased = await svc.lease(worker_id=WORKER, lease_seconds=60)
    assert leased is not None and leased.id == job.id
    assert leased.state == "leased"
    assert leased.lease_owner == WORKER
    assert leased.attempts == 1
    running = await svc.start(job_id=job.id, worker_id=WORKER)
    assert running.state == "running"
    done = await svc.complete(job_id=job.id, worker_id=WORKER, result_ref="ok")
    assert done.state == "succeeded"
    assert done.result_ref == "ok"
    events = [e.event_type for e in await _outbox(svc, job.id)]
    assert events == ["job.enqueued", "job.succeeded"]


async def test_lease_returns_none_when_nothing_eligible(svc: JobService) -> None:
    assert await svc.lease(worker_id=WORKER, lease_seconds=60) is None


async def test_duplicate_complete_does_not_double_transition(svc: JobService) -> None:
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    first = await svc.complete(job_id=job.id, worker_id=WORKER)
    second = await svc.complete(job_id=job.id, worker_id=WORKER)
    assert first.version == second.version  # no further transition
    events = [e.event_type for e in await _outbox(svc, job.id)]
    assert events.count("job.succeeded") == 1  # idempotent publication


async def test_complete_by_wrong_owner_is_refused(svc: JobService) -> None:
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    with pytest.raises(JobStateError):
        await svc.complete(job_id=job.id, worker_id=OTHER_WORKER)


async def test_recoverable_failure_requeues_within_budget(svc: JobService) -> None:
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=3
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    failed = await svc.fail(
        job_id=job.id, worker_id=WORKER, error_code="TRANSIENT", recoverable=True
    )
    assert failed.state == "recoverable_failed"
    assert failed.attempts == 1
    # Eligible again once the backoff window has passed.
    released = await svc.lease(worker_id=WORKER, lease_seconds=60)
    assert released is not None and released.id == job.id
    assert released.attempts == 2


async def test_recoverable_failure_becomes_terminal_at_attempt_budget(
    svc: JobService,
) -> None:
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=1
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    failed = await svc.fail(
        job_id=job.id, worker_id=WORKER, error_code="TRANSIENT", recoverable=True
    )
    assert failed.state == "terminal_failed"
    assert await svc.lease(worker_id=WORKER, lease_seconds=60) is None


async def test_non_recoverable_failure_is_terminal(svc: JobService) -> None:
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=5
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    failed = await svc.fail(
        job_id=job.id, worker_id=WORKER, error_code="BUG", recoverable=False
    )
    assert failed.state == "terminal_failed"
    events = [e.event_type for e in await _outbox(svc, job.id)]
    assert "job.terminal_failed" in events


async def test_expired_lease_is_reaped_to_recoverable(svc: JobService) -> None:
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=3
    )
    past = utc_now() - timedelta(seconds=1)
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    # Force the lease to look expired without waiting.
    await svc._force_lease_expiry(job.id, past)  # test hook, PG uses real clock
    reaped = await svc.reap_expired_leases()
    assert len(reaped) == 1
    refreshed = await svc.get(job.id)
    assert refreshed is not None and refreshed.state == "recoverable_failed"


async def test_cancel_request_then_finalize(svc: JobService) -> None:
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    requested = await svc.request_cancel(job_id=job.id)
    assert requested.state == "cancel_requested"
    cancelled = await svc.finalize_cancel(job_id=job.id)
    assert cancelled.state == "cancelled"
    # A cancelled job is not leasable.
    assert await svc.lease(worker_id=WORKER, lease_seconds=60) is None
    events = [e.event_type for e in await _outbox(svc, job.id)]
    assert events == ["job.enqueued", "job.cancel_requested", "job.cancelled"]


async def test_cannot_complete_a_cancelled_job(svc: JobService) -> None:
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    await svc.request_cancel(job_id=job.id)
    await svc.finalize_cancel(job_id=job.id)
    with pytest.raises(JobStateError):
        await svc.complete(job_id=job.id, worker_id=WORKER)
