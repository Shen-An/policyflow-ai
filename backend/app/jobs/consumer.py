"""T064 [US2] Celery consumer: drain durable jobs off the broker, reap dead peers.

This is the production counterpart to the broker-free :class:`LocalJobRunner`.
A worker registers one Celery task -- :data:`CELERY_JOB_TASK_NAME` -- that the
outbox transport (:mod:`backend.app.jobs.transport`) nudges with a durable-job id.
Each nudge drives one *cycle* against the authoritative ``durable_jobs`` rows:

1. **reap expired leases** -- a peer worker that died (``kill -9``) cannot release
   its own lease, so a surviving worker reclaims it (``JobService.reap_expired_leases``,
   the same sweep the restart-recovery suite exercises). This is the "worker-side
   lease release" half of the story for a crashed peer.
2. **lease + run one** -- lease the next eligible job through the identical
   version-CAS state machine, dispatch it to its handler, and ``complete``/``fail``
   it. ``complete``/``fail`` clear the lease, which is lease release for the happy
   and the domain-failure paths.

The message is only a nudge; the row is the source of truth. With ``acks_late`` +
``task_reject_on_worker_lost`` (see :func:`build_celery_app`) a worker killed
mid-task has its message redelivered, and the idempotent state machine makes the
re-run safe -- the job still reaches exactly one terminal state (no duplicate
transition, no duplicate ``job.completed`` outbox row, which the unique
``(aggregate_type, aggregate_id, aggregate_version, event_type)`` constraint
enforces).

The synchronous Celery task body runs its async work on a single per-process event
loop held by :class:`_AsyncBridge`, so the async engine's connections always bind to
one loop (no "attached to a different loop" hazard across task invocations), which
is correct under the ``solo`` pool used on Windows and under prefork elsewhere.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from celery import Celery
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.db.session import build_async_engine
from backend.app.jobs.runner import JobContext, JobHandlerRegistry, LocalJobRunner
from backend.app.jobs.service import JobService
from backend.app.jobs.transport import CELERY_JOB_TASK_NAME

__all__ = [
    "CELERY_JOB_TASK_NAME",
    "WorkerRuntime",
    "drain_one_cycle",
    "register_job_task",
]


class _AsyncBridge:
    """A single event loop in a background thread for a worker process.

    Celery task bodies are synchronous; our job pipeline is async. Running every
    coroutine on one long-lived loop keeps the async DB engine's pooled
    connections bound to a single loop across task invocations.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="job-consumer-loop", daemon=True
        )
        self._thread.start()

    def run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


@dataclass
class WorkerRuntime:
    """Everything a worker needs to run a drain cycle, built once per process.

    ``engine`` is the async engine bound to the bridge loop; ``context`` carries
    the live dependencies handlers resolve at run time; ``registry`` maps job kind
    to handler. A short ``lease_seconds`` keeps a crashed worker's lease reclaimable
    quickly (reaping runs at the top of every cycle).
    """

    engine: Any
    context: JobContext
    registry: JobHandlerRegistry
    worker_id: str
    lease_seconds: int
    cancel_poll_seconds: float = 0.5

    def session_factory(self) -> async_sessionmaker:
        return async_sessionmaker(self.engine, expire_on_commit=False)


async def drain_one_cycle(runtime: WorkerRuntime) -> int:
    """Reap dead peers' expired leases, then lease and run one eligible job.

    Returns the number of jobs run in this cycle (0 or 1). Reaping first means a
    message redelivered after a peer's ``kill -9`` reclaims that peer's expired
    lease before this worker tries to lease, so the redelivery actually makes
    progress instead of finding the row stuck ``running`` under a dead owner.
    """
    service = JobService(factory=runtime.session_factory())
    await service.reap_expired_leases()
    runner = LocalJobRunner(
        service=service,
        context=runtime.context,
        registry=runtime.registry,
        worker_id=runtime.worker_id,
        lease_seconds=runtime.lease_seconds,
        max_jobs=1,
        cancel_poll_seconds=runtime.cancel_poll_seconds,
    )
    return await runner.drain_once()


def register_job_task(celery_app: Celery, runtime: WorkerRuntime) -> Any:
    """Register the durable-job consumer task on ``celery_app``.

    The task is late-acked (configured app-wide): it is acknowledged only after the
    cycle returns, so a worker killed mid-cycle has its nudge redelivered. The
    runtime's bridge loop runs the async cycle synchronously for the task body.
    """
    bridge = _AsyncBridge()

    @celery_app.task(name=CELERY_JOB_TASK_NAME, bind=True)
    def run_durable_job(self: Any, job_id: str, event_type: str | None = None) -> int:
        return bridge.run(drain_one_cycle(runtime))

    run_durable_job._bridge = bridge  # type: ignore[attr-defined]  # for teardown in tests
    return run_durable_job


def build_worker_runtime(
    *,
    database_url: str,
    registry: JobHandlerRegistry,
    context: JobContext | None = None,
    worker_id: str = "celery-worker",
    lease_seconds: int = 30,
    cancel_poll_seconds: float = 0.5,
    settings: Any | None = None,
) -> WorkerRuntime:
    """Build a :class:`WorkerRuntime` over ``database_url`` with ``registry``.

    ``context`` defaults to a bare context carrying only the async engine; a
    deployment that runs handlers needing live services (document index, eval)
    supplies a context wired to those. ``lease_seconds`` is short by default so a
    crashed worker's lease is reclaimable within one cycle. ``cancel_poll_seconds``
    is the bounded step at which a running job is checked for a cancel request.
    """
    engine = build_async_engine(database_url, settings)
    ctx = context or JobContext(engine=engine)
    return WorkerRuntime(
        engine=engine,
        context=ctx,
        registry=registry,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        cancel_poll_seconds=cancel_poll_seconds,
    )
