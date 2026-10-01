"""T070 [US2] broker-free local executor for durable jobs.

T070 replaces the ephemeral FastAPI ``BackgroundTasks`` that ran long document
indexing with a :class:`~backend.app.db.models.DurableJob` + outbox submission.
The durability and idempotency now live in the DB (``JobService``); this suite
pins the *executor* that drains that durable queue broker-free, so a dev /
single-node deployment keeps running the work without a RabbitMQ consumer.

The honesty boundary (recorded in docs/08): ``LocalJobRunner`` is a
single-process drain. It leases eligible jobs through the identical version-CAS
state machine a Celery consumer (T064) would use, dispatches each to its
registered handler, and completes/fails it through the same transitions -- so
production swaps the broker consumer for the drain nudge over the *same* durable
rows, nothing faked. These tests run on SQLite with no broker and no app.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import Tenant
from backend.app.db.session import build_async_engine
from backend.app.jobs.runner import (
    DOCUMENT_INDEX_KIND,
    JobContext,
    JobHandlerRegistry,
    LocalJobRunner,
)
from backend.app.jobs.service import JobService

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest_asyncio.fixture
async def svc(tmp_path) -> AsyncIterator[JobService]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}"
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


def _runner(service: JobService, registry: JobHandlerRegistry) -> LocalJobRunner:
    return LocalJobRunner(
        service=service,
        registry=registry,
        context=JobContext(engine=None, lightrag_adapter=None, app_state=None),
        worker_id="test-runner",
        lease_seconds=60,
    )


async def test_drain_leases_runs_and_completes(svc: JobService) -> None:
    seen: list[dict[str, Any]] = []

    async def handler(ctx: JobContext, payload: dict[str, Any]) -> str | None:
        seen.append(payload)
        return "receipt-1"

    registry = JobHandlerRegistry()
    registry.register("demo", handler)

    job = await svc.enqueue(
        tenant_id=TENANT, kind="demo", payload={"n": 1}, idempotency_key="k-demo-1"
    )
    processed = await _runner(svc, registry).drain_once()

    assert processed == 1
    assert seen == [{"n": 1}]
    done = await svc.get(job.id)
    assert done is not None
    assert done.state == "succeeded"
    assert done.result_ref == "receipt-1"
    # The job.succeeded outbox event is emitted exactly once by the state machine.
    events = {e.event_type for e in await svc.list_outbox(aggregate_id=job.id)}
    assert {"job.enqueued", "job.succeeded"} <= events


async def test_drain_is_empty_when_nothing_queued(svc: JobService) -> None:
    registry = JobHandlerRegistry()
    assert await _runner(svc, registry).drain_once() == 0


async def test_handler_exception_fails_job_recoverably(svc: JobService) -> None:
    async def boom(ctx: JobContext, payload: dict[str, Any]) -> str | None:
        raise RuntimeError("handler blew up")

    registry = JobHandlerRegistry()
    registry.register("demo", boom)

    job = await svc.enqueue(
        tenant_id=TENANT,
        kind="demo",
        payload={},
        idempotency_key="k-demo-2",
        max_attempts=3,
    )
    processed = await _runner(svc, registry).drain_once()

    # The fixture's JobService uses zero backoff (its unit-contract default), so
    # a recoverable failure is immediately eligible again: the drain re-leases
    # and retries within the same nudge, consuming the attempt budget. That is
    # the honest zero-backoff behaviour -- production sets a positive backoff so
    # a re-queued job is not instantly re-eligible.
    assert processed == 3
    failed = await svc.get(job.id)
    assert failed is not None
    assert failed.state == "terminal_failed"
    assert failed.last_error_code == "RuntimeError"
    assert failed.attempts == 3
    # The budget was walked via recoverable_failed before going terminal.
    events = [e.event_type for e in await svc.list_outbox(aggregate_id=job.id)]
    assert "job.recoverable_failed" in events
    assert events.count("job.terminal_failed") == 1


async def test_unknown_kind_terminally_fails_without_a_handler(svc: JobService) -> None:
    registry = JobHandlerRegistry()  # no handler registered for "mystery"
    job = await svc.enqueue(
        tenant_id=TENANT, kind="mystery", payload={}, idempotency_key="k-demo-3"
    )
    processed = await _runner(svc, registry).drain_once()

    assert processed == 1
    failed = await svc.get(job.id)
    assert failed is not None
    assert failed.state == "terminal_failed"
    assert failed.last_error_code == "NO_HANDLER"


async def test_document_index_kind_is_registered_by_default() -> None:
    from backend.app.jobs.runner import default_registry

    assert default_registry().get(DOCUMENT_INDEX_KIND) is not None
