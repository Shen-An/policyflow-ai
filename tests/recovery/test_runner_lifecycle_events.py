"""T067/T068 [US2] the runner publishes run lifecycle events for the live tail.

The live SSE tail is only useful if a worker actually produces events. These pin
that :class:`LocalJobRunner` publishes the run-facing lifecycle events to its
``JobContext.event_stream`` as a durable job advances -- ``run.started`` then
``run.succeeded`` on success, and ``run.failed`` only on a *terminal* failure (a
recoverable re-queue is not terminal, so the tail must stay open). A fake stream
captures the calls, so no Redis is needed.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.db.session import build_async_engine
from backend.app.jobs.runner import JobContext, JobHandlerRegistry, LocalJobRunner
from backend.app.jobs.service import JobService

TENANT = "11111111-1111-1111-1111-111111111111"


class _FakeStream:
    """Captures (run_id, event_type) publish calls; stands in for RunEventStream."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, run_id: str, event_type: str, data: dict) -> str:
        self.published.append((run_id, event_type))
        return "1-0"


def _runner(jobs, fake: _FakeStream, registry: JobHandlerRegistry):
    engine = build_async_engine(jobs.url)
    svc = JobService(factory=async_sessionmaker(engine, expire_on_commit=False))
    runner = LocalJobRunner(
        service=svc,
        context=JobContext(engine=engine, event_stream=fake),
        registry=registry,
        worker_id="lifecycle-worker",
    )
    return engine, svc, runner


async def test_runner_publishes_started_then_succeeded(jobs) -> None:
    fake = _FakeStream()

    async def _ok(ctx: JobContext, payload: dict) -> str:
        return "done"

    reg = JobHandlerRegistry()
    reg.register("probe", _ok)
    engine, svc, runner = _runner(jobs, fake, reg)
    job = await svc.enqueue(
        tenant_id=TENANT, kind="probe", payload={}, idempotency_key="k1", max_attempts=3
    )

    assert await runner.drain_once() == 1
    assert [etype for _rid, etype in fake.published] == ["run.started", "run.succeeded"]
    expected_run = job.run_id or job.id
    assert all(rid == expected_run for rid, _e in fake.published)
    await engine.dispose()


async def test_runner_publishes_failed_only_on_terminal(jobs) -> None:
    fake = _FakeStream()

    async def _boom(ctx: JobContext, payload: dict) -> str:
        raise RuntimeError("handler failed")

    reg = JobHandlerRegistry()
    reg.register("probe", _boom)
    engine, svc, runner = _runner(jobs, fake, reg)
    # max_attempts=1 -> the single failure is terminal, so run.failed is published.
    job = await svc.enqueue(
        tenant_id=TENANT, kind="probe", payload={}, idempotency_key="k2", max_attempts=1
    )

    await runner.drain_once()
    assert [etype for _rid, etype in fake.published] == ["run.started", "run.failed"]
    final = await svc.get(job.id)
    assert final is not None and final.state == "terminal_failed"
    await engine.dispose()


async def test_runner_recoverable_failure_does_not_publish_terminal(jobs) -> None:
    fake = _FakeStream()

    async def _boom(ctx: JobContext, payload: dict) -> str:
        raise RuntimeError("transient")

    reg = JobHandlerRegistry()
    reg.register("probe", _boom)
    engine = build_async_engine(jobs.url)
    svc = JobService(factory=async_sessionmaker(engine, expire_on_commit=False))
    # max_jobs=1 so this drain runs exactly one attempt (with 0 backoff a re-queued
    # job would otherwise be re-leased immediately within the same drain).
    runner = LocalJobRunner(
        service=svc,
        context=JobContext(engine=engine, event_stream=fake),
        registry=reg,
        worker_id="lifecycle-worker",
        max_jobs=1,
    )
    # max_attempts=3 -> the first failure re-queues (recoverable), NOT terminal, so
    # only run.started is published; the live tail must stay open for the retry.
    await svc.enqueue(
        tenant_id=TENANT, kind="probe", payload={}, idempotency_key="k3", max_attempts=3
    )

    await runner.drain_once()
    assert [etype for _rid, etype in fake.published] == ["run.started"]
    await engine.dispose()
