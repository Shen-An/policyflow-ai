"""T064 [US2] consumer drain-cycle logic (broker-free unit level).

The live-broker round-trip and the kill/redelivery drill live in
``tests/integration/test_consumer_live.py`` (they need RabbitMQ + a worker
subprocess). These tests pin the *logic* of one consumer cycle on the always-
available SQLite path, with no broker: a cycle leases and runs one eligible job
through the real state machine, and -- because it reaps first -- it reclaims a
dead peer's expired lease before running it. That reap-then-run order is what lets
a redelivered nudge make progress after a peer's ``kill -9`` instead of finding
the row stuck ``running`` under a dead owner.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from backend.app.db.models import utc_now
from backend.app.jobs.consumer import build_worker_runtime, drain_one_cycle
from backend.app.jobs.runner import JobContext, JobHandlerRegistry

TENANT = "11111111-1111-1111-1111-111111111111"
PROBE_KIND = "recovery_probe"


def _runtime_with_probe(url: str, ran: list[str], *, lease_seconds: int = 30):
    async def _probe(ctx: JobContext, payload: dict) -> str | None:
        ran.append(payload.get("token", "?"))
        return f"ran:{payload.get('token', '?')}"

    registry = JobHandlerRegistry()
    registry.register(PROBE_KIND, _probe)
    return build_worker_runtime(
        database_url=url,
        registry=registry,
        worker_id="celery-worker-test",
        lease_seconds=lease_seconds,
    )


async def test_cycle_leases_runs_and_completes_one_job(jobs) -> None:
    ran: list[str] = []
    runtime = _runtime_with_probe(jobs.url, ran)
    producer = jobs.fresh_service()
    job = await producer.enqueue(
        tenant_id=TENANT, kind=PROBE_KIND, payload={"token": "t1"},
        idempotency_key="k1", max_attempts=3,
    )

    count = await drain_one_cycle(runtime)

    assert count == 1
    assert ran == ["t1"]
    refreshed = await jobs.fresh_service().get(job.id)
    assert refreshed is not None
    assert refreshed.state == "succeeded"
    assert refreshed.result_ref == "ran:t1"
    await runtime.engine.dispose()


async def test_cycle_reaps_a_dead_peers_lease_then_runs_it(jobs) -> None:
    ran: list[str] = []
    runtime = _runtime_with_probe(jobs.url, ran)
    producer = jobs.fresh_service()
    job = await producer.enqueue(
        tenant_id=TENANT, kind=PROBE_KIND, payload={"token": "t2"},
        idempotency_key="k2", max_attempts=3,
    )
    # A peer worker leases the job, then dies (its lease is already in the past).
    dead = jobs.fresh_service()
    await dead.lease(worker_id="dead-peer", lease_seconds=60)
    await dead._force_lease_expiry(job.id, utc_now() - timedelta(seconds=1))

    # The cycle reaps the dead peer's lease, then leases+runs the reclaimed job.
    count = await drain_one_cycle(runtime)

    assert count == 1
    assert ran == ["t2"]
    refreshed = await jobs.fresh_service().get(job.id)
    assert refreshed is not None and refreshed.state == "succeeded"
    assert refreshed.attempts == 2  # one failed lease (dead peer) + this run
    await runtime.engine.dispose()


async def test_cycle_with_no_eligible_job_is_a_noop(jobs) -> None:
    ran: list[str] = []
    runtime = _runtime_with_probe(jobs.url, ran)

    count = await drain_one_cycle(runtime)

    assert count == 0
    assert ran == []
    await runtime.engine.dispose()


async def test_cycle_cancels_a_running_job_cooperatively(jobs) -> None:
    # A long-running handler is cancelled mid-flight when request_cancel moves the
    # job to cancel_requested; the consumer observes it at its bounded poll step and
    # finalizes the job as cancelled rather than completing it.
    completed: list[str] = []

    async def _slow(ctx: JobContext, payload: dict) -> str | None:
        await asyncio.sleep(payload.get("sleep_ms", 0) / 1000)
        completed.append("finished")  # must NOT run: we cancel before the sleep ends
        return "done"

    registry = JobHandlerRegistry()
    registry.register(PROBE_KIND, _slow)
    runtime = build_worker_runtime(
        database_url=jobs.url, registry=registry, worker_id="celery-worker-test",
        lease_seconds=30, cancel_poll_seconds=0.1,
    )
    producer = jobs.fresh_service()
    job = await producer.enqueue(
        tenant_id=TENANT, kind=PROBE_KIND, payload={"sleep_ms": 10000},
        idempotency_key="cancel", max_attempts=3,
    )
    cycle = asyncio.ensure_future(drain_one_cycle(runtime))
    try:
        svc = jobs.fresh_service()
        running = None
        for _ in range(50):
            await asyncio.sleep(0.1)
            running = await svc.get(job.id)
            if running is not None and running.state == "running":
                break
        assert running is not None and running.state == "running"

        await jobs.fresh_service().request_cancel(job_id=job.id)
        await asyncio.wait_for(cycle, timeout=10)

        final = await jobs.fresh_service().get(job.id)
        assert final is not None and final.state == "cancelled"
        assert completed == []  # the handler was cancelled before finishing
    finally:
        if not cycle.done():
            cycle.cancel()
        await runtime.engine.dispose()
