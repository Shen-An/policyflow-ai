"""T070 [US2] broker-free local executor for durable jobs.

This closes the design decision T070 was waiting on. The long-running document
indexing that previously rode an ephemeral FastAPI ``BackgroundTask`` is now
submitted as a :class:`~backend.app.db.models.DurableJob` with a transactional
outbox event (``JobService``): durability, idempotency and the observable
``job.enqueued`` event move into the database, so the work survives a process
restart and a redelivery can never drive a duplicate side effect.

What still has to *run* the queued job broker-free is the honest part. In
production a Celery consumer (T064) leases the row off the broker; that path is
gated on RabbitMQ (T072). :class:`LocalJobRunner` is the single-process drain
that keeps a dev / single-node deployment working without a broker: it leases
eligible jobs through the *identical* version-CAS state machine the consumer
would use, dispatches each to its registered handler, and completes or fails it
through the same transitions. Production swaps the broker consumer in for the
drain nudge over the same durable rows -- nothing is faked, and the durable
record is authoritative either way.

The lease scan is global by construction, which is correct for a single-process
drain and is exactly what the broker consumer replaces. Durable payloads carry
only JSON ids; the live objects a handler needs (the sync engine, the LightRAG
adapter, app state) are resolved from :class:`JobContext` when the job actually
runs, never serialized into the row.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.app.jobs.service import JobService, JobStateError

__all__ = [
    "DOCUMENT_INDEX_KIND",
    "JobContext",
    "JobHandler",
    "JobHandlerRegistry",
    "LocalJobRunner",
    "default_registry",
    "submit_document_index",
]

#: Durable job kind for the document-indexing work T070 lifts off BackgroundTasks.
DOCUMENT_INDEX_KIND = "document_index"


@dataclass
class JobContext:
    """Live dependencies a handler resolves at execution time.

    The durable row stores only JSON ids; the live objects are supplied here so
    the same handler runs identically under the local drain and under a future
    broker consumer.
    """

    engine: Any
    lightrag_adapter: Any | None = None
    app_state: Any | None = None


#: A handler runs one durable job and returns an optional ``result_ref``.
JobHandler = Callable[[JobContext, dict[str, Any]], Awaitable[str | None]]


class JobHandlerRegistry:
    """Maps a durable job ``kind`` to the coroutine that executes it."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, kind: str, handler: JobHandler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: str) -> JobHandler | None:
        return self._handlers.get(kind)


async def _handle_document_index(ctx: JobContext, payload: dict[str, Any]) -> str | None:
    """Index one document, resolving the live LightRAG adapter from the context.

    ``process_document_index`` owns the domain idempotency (it claims the pending
    ``RagIndexJob`` by document id) and records index success/failure on the
    document row. The durable job therefore tracks "the index attempt ran"; a
    domain-level index failure stays on the document row, matching the legacy
    BackgroundTask semantics. Returning without an adapter is an honest no-op --
    there is nothing to index into.
    """
    indexer = ctx.lightrag_adapter
    if indexer is None:
        return None
    # Imported lazily to keep this module free of the service/model import graph.
    from backend.app.services.indexing_service import process_document_index

    await process_document_index(ctx.engine, indexer, payload["document_id"])
    return None


def default_registry() -> JobHandlerRegistry:
    """Build the registry wired for the kinds T070 migrates off BackgroundTasks."""
    registry = JobHandlerRegistry()
    registry.register(DOCUMENT_INDEX_KIND, _handle_document_index)
    return registry


_DEFAULT_REGISTRY = default_registry()


class LocalJobRunner:
    """Broker-free single-process drain for the durable job queue."""

    def __init__(
        self,
        *,
        service: JobService,
        context: JobContext,
        registry: JobHandlerRegistry | None = None,
        worker_id: str = "local-runner",
        lease_seconds: int = 300,
        max_jobs: int = 1000,
    ) -> None:
        self._service = service
        self._registry = registry or _DEFAULT_REGISTRY
        self._context = context
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_jobs = max_jobs

    async def drain_once(self) -> int:
        """Lease and run eligible jobs until none remain. Returns the count run.

        Bounded by ``max_jobs`` so a handler that keeps re-queuing cannot spin
        the drain forever within one nudge; the next nudge resumes the backlog.
        """
        processed = 0
        while processed < self._max_jobs:
            job = await self._service.lease(
                worker_id=self._worker_id, lease_seconds=self._lease_seconds
            )
            if job is None:
                break
            await self._run_one(job.id, job.kind, dict(job.payload or {}))
            processed += 1
        return processed

    async def _run_one(self, job_id: str, kind: str, payload: dict[str, Any]) -> None:
        handler = self._registry.get(kind)
        if handler is None:
            # No executor for this kind: fail terminally rather than hold a lease
            # that will only expire and re-queue into the same dead end.
            await self._safe_fail(job_id, "NO_HANDLER", recoverable=False)
            return
        try:
            await self._service.start(job_id=job_id, worker_id=self._worker_id)
            result_ref = await handler(self._context, payload)
        except Exception as exc:  # handler (or start) failure -> recoverable retry
            await self._safe_fail(job_id, type(exc).__name__, recoverable=True)
            return
        try:
            await self._service.complete(
                job_id=job_id, worker_id=self._worker_id, result_ref=result_ref
            )
        except JobStateError:
            # Lost the lease (e.g. reaped) between running and completing; the
            # authoritative row already moved on. Nothing to force here.
            pass

    async def _safe_fail(self, job_id: str, error_code: str, *, recoverable: bool) -> None:
        try:
            await self._service.fail(
                job_id=job_id,
                worker_id=self._worker_id,
                error_code=error_code,
                recoverable=recoverable,
            )
        except JobStateError:
            pass


def _resolve_job_service(app: Any) -> JobService:
    """JobService over the app's async engine, honouring a test/prod override."""
    override = getattr(app.state, "job_service", None)
    if override is not None:
        return override
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(app.state.async_engine, expire_on_commit=False)
    return JobService(factory=factory)


def _resolve_runner(app: Any, service: JobService) -> LocalJobRunner:
    override = getattr(app.state, "job_runner", None)
    if override is not None:
        return override
    registry = getattr(app.state, "job_handler_registry", None) or _DEFAULT_REGISTRY
    context = JobContext(
        engine=app.state.engine,
        lightrag_adapter=getattr(app.state, "lightrag_adapter", None),
        app_state=app.state,
    )
    return LocalJobRunner(service=service, context=context, registry=registry)


async def submit_document_index(
    *,
    app: Any,
    background_tasks: Any,
    tenant_id: str,
    document_id: str,
    idempotency_key: str,
) -> None:
    """Durably submit a document-index job and schedule a broker-free drain.

    Replaces ``background_tasks.add_task(process_document_index, ...)``. The
    durable enqueue (idempotency + ``job.enqueued`` outbox) is awaited inline so
    the intent is persisted before the response returns; the BackgroundTask is
    now only the single-process drain *nudge*, which production replaces with the
    Celery consumer (T064) over the same durable row. The nudge keeps the request
    non-blocking: the long LightRAG insert runs after the response is sent.
    """
    service = _resolve_job_service(app)
    await service.enqueue(
        tenant_id=tenant_id or "",
        kind=DOCUMENT_INDEX_KIND,
        payload={"document_id": document_id},
        idempotency_key=idempotency_key,
    )
    runner = _resolve_runner(app, service)
    background_tasks.add_task(runner.drain_once)
