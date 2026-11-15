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

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from backend.app.jobs.service import JobService, JobStateError
from backend.app.sse.run_events import (
    RUN_CANCELLED,
    RUN_FAILED,
    RUN_STARTED,
    RUN_SUCCEEDED,
)

__all__ = [
    "DOCUMENT_INDEX_KIND",
    "EVAL_RUN_KIND",
    "JobContext",
    "JobHandler",
    "JobHandlerRegistry",
    "LocalJobRunner",
    "default_registry",
    "submit_document_index",
    "submit_document_index_batch",
    "submit_eval_run",
]

#: Durable job kind for the document-indexing work T070 lifts off BackgroundTasks.
DOCUMENT_INDEX_KIND = "document_index"
#: Durable job kind for the long-running evaluation run T070 lifts off BackgroundTasks.
EVAL_RUN_KIND = "eval_run"


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
    #: Optional RunEventStream (Redis). When present the runner publishes run
    #: lifecycle events (started/succeeded/failed/cancelled) so connected SSE
    #: clients see the run advance live. None on a broker-free dev drain.
    event_stream: Any | None = None
    tenant_id: str = ""
    run_id: str = ""
    job_id: str = ""


class JobDependencyError(RuntimeError):
    """A required business dependency is absent; never report a successful no-op."""


class JobDomainError(RuntimeError):
    """The domain operation failed terminally or its input is no longer valid."""


@dataclass(frozen=True)
class BusinessJobResult:
    result_ref: str
    # Business handlers own their durable lifecycle, including approval pauses.
    # Completing a preparation job does not complete the logical business run.


#: A handler runs one durable job and returns an optional ``result_ref``.
JobHandler = Callable[[JobContext, dict[str, Any]], Awaitable[str | BusinessJobResult | None]]


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
    document row. The durable job only succeeds when that row is indexed;
    missing dependencies and domain failures are terminal job failures.
    """
    indexer = ctx.lightrag_adapter
    if indexer is None:
        raise JobDependencyError("document_index requires a LightRAG adapter")
    # Imported lazily to keep this module free of the service/model import graph.
    from sqlmodel import Session

    from backend.app.db.models import KnowledgeDocument
    from backend.app.services.indexing_service import process_document_index

    document_id = payload["document_id"]
    with Session(ctx.engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None or document.tenant_id != ctx.tenant_id:
            raise JobDomainError("document does not belong to this job's tenant")
    await process_document_index(ctx.engine, indexer, document_id)
    with Session(ctx.engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None or document.index_status != "indexed":
            raise JobDomainError("document indexing did not succeed")
    return None


async def _handle_eval_run(ctx: JobContext, payload: dict[str, Any]) -> str | None:
    """Execute one evaluation run, resolving live services from the context.

    The durable payload carries only JSON ids and the serialized ``EvalRunCreate``
    request; the RAG service, agent pipeline and optional graph route adapter are
    resolved from ``ctx.app_state`` at execution time (never serialized). The
    handler verifies the persisted domain outcome before completing the job.
    """
    app_state = ctx.app_state
    if app_state is None:
        raise JobDependencyError("eval_run requires application business services")
    # Imported lazily to keep this module free of the service/schema import graph.
    from backend.app.schemas.eval import EvalRunCreate
    from backend.app.services.eval_service import execute_eval_run

    data = EvalRunCreate.model_validate(payload["data"])
    run_id = payload["run_id"]
    tenant_id = ctx.tenant_id
    from sqlmodel import Session

    from backend.app.db.models import EvalRun

    with Session(ctx.engine) as session:
        row = session.get(EvalRun, run_id)
        if row is None or row.tenant_id != tenant_id:
            raise JobDomainError("evaluation does not belong to this job's tenant")
    rag_service = app_state.rag_service
    pipeline = app_state.agent_pipeline
    settings = app_state.settings
    adapter = getattr(app_state, "graph_route_adapter", None)
    if settings.ROUTE_VIA_GRAPH_ADAPTER and adapter is not None:
        await adapter.run_eval(
            engine=ctx.engine,
            rag_service=rag_service,
            pipeline=pipeline,
            run_id=run_id,
            data=data,
            tenant_id=tenant_id,
        )
    else:
        await execute_eval_run(ctx.engine, rag_service, pipeline, run_id, data)
    with Session(ctx.engine) as session:
        row = session.get(EvalRun, run_id)
        if row is None or row.status not in {"success", "skipped"}:
            raise JobDomainError("evaluation did not succeed")
    return None


def default_registry() -> JobHandlerRegistry:
    """Build the registry wired for the kinds T070 migrates off BackgroundTasks."""
    registry = JobHandlerRegistry()
    registry.register(DOCUMENT_INDEX_KIND, _handle_document_index)
    registry.register(EVAL_RUN_KIND, _handle_eval_run)
    from backend.app.jobs.business import handle_chat, handle_file_resume, handle_file_workflow
    registry.register("chat", handle_chat)
    registry.register("file_workflow", handle_file_workflow)
    registry.register("file_workflow_resume", handle_file_resume)
    return registry


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
        cancel_poll_seconds: float | None = None,
        lanes: Sequence[str] | None = None,
    ) -> None:
        self._service = service
        self._registry = registry or default_registry()
        self._context = context
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_jobs = max_jobs
        # Priority lanes this worker serves. None keeps the historical behaviour
        # (lease any lane); a tuple makes the drain honest about lane isolation so
        # a bulk backlog cannot delay an interactive job on the same queue.
        self._lanes = tuple(lanes) if lanes is not None else None
        # When set, a running job is checked for cooperative cancellation every
        # ``cancel_poll_seconds`` (the "bounded step"): if ``request_cancel`` has
        # moved it to ``cancel_requested`` the handler is cancelled and the job is
        # finalized ``cancelled`` instead of completed. None keeps the plain path.
        self._cancel_poll_seconds = cancel_poll_seconds

    async def drain_once(self) -> int:
        """Lease and run eligible jobs until none remain. Returns the count run.

        Bounded by ``max_jobs`` so a handler that keeps re-queuing cannot spin
        the drain forever within one nudge; the next nudge resumes the backlog.
        """
        processed = 0
        while processed < self._max_jobs:
            job = await self._service.lease(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                lanes=self._lanes,
            )
            if job is None:
                break
            await self._run_one(
                job.id, job.kind, dict(job.payload or {}), run_id=job.run_id or job.id
            )
            processed += 1
        return processed

    async def _run_one(
        self, job_id: str, kind: str, payload: dict[str, Any], *, run_id: str
    ) -> None:
        handler = self._registry.get(kind)
        if handler is None:
            # No executor for this kind: fail terminally rather than hold a lease
            # that will only expire and re-queue into the same dead end.
            await self._safe_fail(job_id, "NO_HANDLER", recoverable=False, run_id=run_id)
            return
        job = await self._service.get(job_id)
        context = replace(self._context, tenant_id=job.tenant_id, run_id=run_id, job_id=job_id)
        try:
            await self._service.start(job_id=job_id, worker_id=self._worker_id)
            await self._publish_lifecycle(run_id, RUN_STARTED, {"job_id": job_id}, tenant_id=context.tenant_id)
            if self._cancel_poll_seconds is not None:
                result_ref, cancelled = await self._run_with_cancellation(
                    job_id, handler, payload, context
                )
                if cancelled:
                    await self._safe_finalize_cancel(job_id)
                    await self._publish_lifecycle(run_id, RUN_CANCELLED, {}, tenant_id=context.tenant_id)
                    return
            else:
                result_ref = await handler(context, payload)
        except JobDependencyError:
            await self._safe_fail(job_id, "MISSING_DEPENDENCY", recoverable=False, run_id=run_id)
            return
        except (JobDomainError, ValueError, PermissionError):
            await self._safe_fail(job_id, "DOMAIN_FAILED", recoverable=False, run_id=run_id)
            return
        except Exception as exc:  # handler (or start) failure -> recoverable retry
            logging.getLogger(__name__).exception("Durable handler failed: kind=%s job_id=%s", kind, job_id)
            await self._safe_fail(job_id, type(exc).__name__, recoverable=True, run_id=run_id)
            return
        try:
            await self._service.complete(
                job_id=job_id, worker_id=self._worker_id,
                result_ref=result_ref.result_ref if isinstance(result_ref, BusinessJobResult) else result_ref,
            )
        except JobStateError:
            # Lost the lease (e.g. reaped) between running and completing; the
            # authoritative row already moved on. Nothing to force here.
            return
        if not isinstance(result_ref, BusinessJobResult):
            await self._publish_lifecycle(run_id, RUN_SUCCEEDED, {}, tenant_id=context.tenant_id)

    async def _publish_lifecycle(
        self, run_id: str, event_type: str, data: dict[str, Any], *, tenant_id: str
    ) -> None:
        """Best-effort publish of a run lifecycle event to the run's event stream.

        No-op when no stream is wired (a broker-free dev drain). Errors are
        swallowed: the SSE stream is an observability channel, so it must never
        fail the job it is describing -- the durable row stays the authority.
        """
        state = self._context.app_state
        if state is not None and callable(getattr(state, "uow_factory", None)):
            from sqlalchemy import select

            from backend.app.db.models import AgentRun
            from backend.app.jobs.business import event, save
            async with state.uow_factory() as uow:
                await uow.set_tenant_context(tenant_id)
                row = await uow.session.scalar(select(AgentRun).where(
                    AgentRun.tenant_id == tenant_id, AgentRun.run_id == run_id))
            if row is not None:
                ctx = replace(self._context, tenant_id=row.tenant_id, run_id=run_id)
                terminal = {RUN_FAILED: "terminal_failed", RUN_CANCELLED: "cancelled"}.get(event_type)
                if terminal and row.status not in {"succeeded", "cancelled", "terminal_failed"}:
                    await save(ctx, status=terminal, snapshot={**row.result_snapshot, **data},
                               milestones=[(event_type, data)])
                elif not terminal:
                    await event(ctx, event_type, data, "running")
        stream = getattr(self._context, "event_stream", None)
        if stream is None:
            return
        try:
            await stream.publish(run_id, event_type, data)
        except Exception:  # noqa: BLE001 - observability must not break the run
            pass

    async def _run_with_cancellation(
        self, job_id: str, handler: JobHandler, payload: dict[str, Any], context: JobContext
    ) -> tuple[str | BusinessJobResult | None, bool]:
        """Run ``handler`` while polling for a cooperative cancel request.

        Returns ``(result_ref, cancelled)``. The handler runs as a task; every
        ``cancel_poll_seconds`` the job's authoritative state is checked. If it has
        moved to ``cancel_requested`` the handler task is cancelled and ``cancelled``
        is True. A handler that finishes first wins, except that a cancel requested
        in the same window is still honoured (so a late finish does not slip past a
        requested cancel and then fail to ``complete`` out of ``cancel_requested``).
        A genuine handler exception propagates to the caller's failure path.
        """
        handler_task = asyncio.ensure_future(handler(context, payload))
        while True:
            done, _ = await asyncio.wait(
                {handler_task}, timeout=self._cancel_poll_seconds
            )
            if handler_task in done:
                result_ref = handler_task.result()  # re-raises a handler exception
                job = await self._service.get(job_id)
                if job is not None and job.state == "cancel_requested":
                    return None, True
                return result_ref, False
            job = await self._service.get(job_id)
            if job is not None and job.state == "cancel_requested":
                handler_task.cancel()
                try:
                    await handler_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                return None, True

    async def _safe_finalize_cancel(self, job_id: str) -> None:
        try:
            await self._service.finalize_cancel(job_id=job_id)
        except JobStateError:
            pass

    async def _safe_fail(
        self, job_id: str, error_code: str, *, recoverable: bool, run_id: str
    ) -> None:
        try:
            job = await self._service.fail(
                job_id=job_id,
                worker_id=self._worker_id,
                error_code=error_code,
                recoverable=recoverable,
            )
        except JobStateError:
            return
        # Only a terminal failure ends the run; a recoverable re-queue is not
        # terminal, so the live tail must stay open for the retry.
        if job.state == "terminal_failed":
            await self._publish_lifecycle(run_id, RUN_FAILED, {"error_code": error_code}, tenant_id=job.tenant_id)


def _resolve_job_service(app: Any) -> JobService:
    """JobService over the app's async engine, honouring a test/prod override."""
    override = getattr(app.state, "job_service", None)
    if override is not None:
        return override
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.jobs.service import retry_tuning_from_settings

    factory = async_sessionmaker(app.state.async_engine, expire_on_commit=False)
    settings = getattr(app.state, "settings", None)
    tuning = retry_tuning_from_settings(settings) if settings is not None else {}
    return JobService(factory=factory, **tuning)


def _resolve_runner(app: Any, service: JobService) -> LocalJobRunner:
    override = getattr(app.state, "job_runner", None)
    if override is not None:
        return override
    registry = getattr(app.state, "job_handler_registry", None) or default_registry()
    context = JobContext(
        engine=app.state.engine,
        lightrag_adapter=getattr(app.state, "lightrag_adapter", None),
        app_state=app.state,
        event_stream=getattr(app.state, "run_event_stream", None),
    )
    return LocalJobRunner(service=service, context=context, registry=registry, cancel_poll_seconds=0.25)


def _nudge_local(app: Any, service: JobService, background_tasks: Any) -> None:
    if not getattr(getattr(app.state, "settings", None), "CELERY_BROKER_URL", None):
        background_tasks.add_task(_resolve_runner(app, service).drain_once)


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
    _nudge_local(app, service, background_tasks)


async def submit_document_index_batch(
    *,
    app: Any,
    background_tasks: Any,
    tenant_id: str,
    items: Any,
) -> int:
    """Durably submit many document-index jobs, then nudge the drain *once*.

    ``items`` is an iterable of ``(document_id, idempotency_key)`` pairs (one per
    pending ``RagIndexJob``). Every intent is persisted inline before the response
    returns; a single drain nudge then drains the whole batch (``drain_once`` runs
    every eligible job), so an import of many documents does not schedule one
    BackgroundTask per document. Returns the number of jobs enqueued.
    """
    service = _resolve_job_service(app)
    enqueued = 0
    for document_id, idempotency_key in items:
        await service.enqueue(
            tenant_id=tenant_id or "",
            kind=DOCUMENT_INDEX_KIND,
            payload={"document_id": document_id},
            idempotency_key=idempotency_key,
        )
        enqueued += 1
    if enqueued:
        _nudge_local(app, service, background_tasks)
    return enqueued


async def submit_eval_run(
    *,
    app: Any,
    background_tasks: Any,
    tenant_id: str,
    run_id: str,
    data: Any,
) -> None:
    """Durably submit a long-running evaluation run and nudge the drain.

    Replaces ``background_tasks.add_task(execute_eval_run / adapter.run_eval, ...)``.
    The ``EvalRun`` id keys idempotency so a redelivered submit of the same run
    dedups, while each freshly created run (new ``EvalRun``) enqueues its own
    durable job. The ``EvalRunCreate`` request is serialized into the payload
    (JSON only); the live RAG service / pipeline / route adapter are resolved from
    app state when the job runs. The drain nudge keeps the request non-blocking.
    """
    service = _resolve_job_service(app)
    await service.enqueue(
        tenant_id=tenant_id or "",
        kind=EVAL_RUN_KIND,
        payload={
            "run_id": str(run_id),
            "tenant_id": tenant_id or "",
            "data": data.model_dump(mode="json"),
        },
        idempotency_key=str(run_id),
    )
    _nudge_local(app, service, background_tasks)
