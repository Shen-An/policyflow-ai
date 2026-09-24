"""T056 [US2] run/worker restart recovery.

When an API or worker instance is killed mid-flight, its leased and running jobs
must not be stranded: a recovery sweep on any surviving/restarted instance
reclaims them (re-queue within the attempt budget, terminal at the budget), and
a freshly started instance can pick the reclaimed work back up. Because the
service object holds no state, these tests build a *new* engine over the same
database to model the restart — correctness comes from PostgreSQL/SQLite, never
from process memory.
"""

from __future__ import annotations

from datetime import timedelta

from backend.app.db.models import utc_now

# TENANT matches the tenant seeded by the `jobs` fixture in conftest.py.
TENANT = "11111111-1111-1111-1111-111111111111"
WORKER = "worker-a"


async def test_leased_job_of_dead_worker_is_reclaimed_and_reassigned(jobs) -> None:
    producer = jobs.fresh_service()
    job = await producer.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"},
        idempotency_key="k", max_attempts=3,
    )
    # A worker leases the job, then its instance dies before starting work.
    dying = jobs.fresh_service()
    await dying.lease(worker_id=WORKER, lease_seconds=60)
    await dying._force_lease_expiry(job.id, utc_now() - timedelta(seconds=1))

    # A restarted instance runs the recovery sweep and reclaims the job.
    restarted = jobs.fresh_service()
    reaped = await restarted.reap_expired_leases()
    assert [j.id for j in reaped] == [job.id]
    refreshed = await restarted.get(job.id)
    assert refreshed is not None and refreshed.state == "recoverable_failed"

    # A fresh worker can lease the reclaimed job; attempts carried across restart.
    released = await restarted.lease(worker_id="worker-b", lease_seconds=60)
    assert released is not None and released.id == job.id
    assert released.attempts == 2


async def test_running_job_is_reaped_on_restart(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=3
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)  # now running
    await svc._force_lease_expiry(job.id, utc_now() - timedelta(seconds=1))

    reaped = await jobs.fresh_service().reap_expired_leases()
    assert [j.id for j in reaped] == [job.id]
    refreshed = await jobs.fresh_service().get(job.id)
    assert refreshed is not None and refreshed.state == "recoverable_failed"


async def test_reap_at_attempt_budget_is_terminal(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=1
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)  # attempts -> 1 == budget
    await svc._force_lease_expiry(job.id, utc_now() - timedelta(seconds=1))
    reaped = await jobs.fresh_service().reap_expired_leases()
    assert [j.id for j in reaped] == [job.id]
    refreshed = await jobs.fresh_service().get(job.id)
    assert refreshed is not None and refreshed.state == "terminal_failed"
    # Terminal jobs are never handed back out.
    assert await jobs.fresh_service().lease(worker_id=WORKER, lease_seconds=60) is None


async def test_succeeded_job_is_untouched_by_recovery_sweep(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(tenant_id=TENANT, kind="k", payload={}, idempotency_key="k")
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc.start(job_id=job.id, worker_id=WORKER)
    await svc.complete(job_id=job.id, worker_id=WORKER)
    assert await jobs.fresh_service().reap_expired_leases() == []
    refreshed = await jobs.fresh_service().get(job.id)
    assert refreshed is not None and refreshed.state == "succeeded"


async def test_recovery_sweep_is_idempotent(jobs) -> None:
    svc = jobs.fresh_service()
    job = await svc.enqueue(
        tenant_id=TENANT, kind="k", payload={}, idempotency_key="k", max_attempts=3
    )
    await svc.lease(worker_id=WORKER, lease_seconds=60)
    await svc._force_lease_expiry(job.id, utc_now() - timedelta(seconds=1))
    first = await jobs.fresh_service().reap_expired_leases()
    second = await jobs.fresh_service().reap_expired_leases()
    assert len(first) == 1 and second == []  # nothing left expired to reclaim
    events = [
        e.event_type
        for e in await jobs.fresh_service().list_outbox(aggregate_id=job.id)
    ]
    assert events.count("job.recoverable_failed") == 1  # no duplicate emission
