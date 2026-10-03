"""T069 [US2] ``/api/v2/runs`` contract: durable enqueue, idempotency, overload.

This is the tenant-scoped HTTP surface for submitting long-running work as a
:class:`~backend.app.db.models.DurableJob` instead of an ephemeral FastAPI
``BackgroundTask``. Three contract points matter for User Story 2 ("stable use
under large-scale concurrency"):

* **Idempotency-Key** — the caller supplies a key of ``16..128`` chars; a
  malformed key is rejected ``400`` *before* any work is enqueued, and a repeat
  of the same ``(tenant, kind, key)`` returns the existing run rather than
  creating a second one (enforced by :meth:`JobService.enqueue`).
* **Overload** — admission is an injectable dependency. When it declines, the
  route returns ``429``/``503`` with a ``Retry-After`` header. The header is set
  by returning a :class:`JSONResponse` directly, because the application error
  handler intentionally does not forward custom headers.
* **Tenant isolation** — the tenant and user come only from the signed token's
  membership (:data:`PrincipalDep`), never from the body; ``GET`` of another
  tenant's run is a ``404``, not a leak.

Admission defaults to allow-all. The production binding to the Redis
token-bucket / lease-semaphore coordinator (T066) is deferred and gated: it is
wired via ``app.state.run_admission`` when a reachable Redis is configured, so
this contract is verifiable PG-/broker-free on SQLite without pretending the
live quota path ran.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from backend.app.api.deps import PrincipalDep
from backend.app.core.exceptions import ApplicationError, NotFoundError
from backend.app.db.models import DurableJob
from backend.app.jobs.service import (
    JobIdempotencyConflict,
    JobService,
    JobStateError,
    retry_tuning_from_settings,
)
from backend.app.observability import telemetry
from backend.app.sse.endpoint import sse_event_source
from backend.app.sse.snapshot import DurableRunSnapshot
from backend.app.sse.stream import RunEventStream

router = APIRouter(prefix="/api/v2", tags=["v2", "runs"])

#: Idempotency-Key length bounds (inclusive), per the T069 contract.
IDEMPOTENCY_KEY_MIN = 16
IDEMPOTENCY_KEY_MAX = 128


@dataclass(frozen=True)
class AdmissionOutcome:
    """Result of an admission check. ``retry_after_seconds`` is advisory."""

    admitted: bool
    reason: str = "admitted"
    http_status: int = 200
    error_code: str = "admitted"
    retry_after_seconds: float = 0.0


class RunAdmission(Protocol):
    """Decides whether a run may be enqueued now. Never raises for a decline."""

    async def admit(self, *, resource: str, identity: str) -> AdmissionOutcome: ...


class _AllowAllAdmission:
    """Default admission: always admits. Overridden in prod when Redis is up."""

    async def admit(self, *, resource: str, identity: str) -> AdmissionOutcome:
        return AdmissionOutcome(admitted=True)


_ALLOW_ALL = _AllowAllAdmission()


def _admission(request: Request) -> RunAdmission:
    return getattr(request.app.state, "run_admission", None) or _ALLOW_ALL


def _job_service(request: Request) -> JobService:
    """Build a JobService over the app's engine (test override honoured)."""
    override = getattr(request.app.state, "job_service", None)
    if override is not None:
        return override
    factory = async_sessionmaker(
        request.app.state.async_engine, expire_on_commit=False
    )
    return JobService(
        factory=factory,
        **retry_tuning_from_settings(request.app.state.settings),
    )


def _run_event_stream(request: Request) -> RunEventStream:
    """Return the run event stream, honouring an injected test/prod override.

    Tests inject ``app.state.run_event_stream`` (a :class:`RunEventStream` over a
    real Redis) directly. In a deployment we build one lazily from the configured
    ``REDIS_URL`` and cache it on ``app.state`` so a client is not created per
    request. ``redis.asyncio`` is imported here rather than at module import so
    the runs contract stays importable on hosts without the optional dependency.
    """
    override = getattr(request.app.state, "run_event_stream", None)
    if override is not None:
        return override
    import redis.asyncio as redis_asyncio  # optional infra dependency

    settings = request.app.state.settings
    client = redis_asyncio.from_url(settings.REDIS_URL)
    stream = RunEventStream(
        client,
        prefix=settings.REDIS_SSE_STREAM_PREFIX,
        max_events=settings.SSE_REPLAY_MAX_EVENTS_PER_RUN,
        ttl_seconds=settings.SSE_REPLAY_TTL_SECONDS,
    )
    request.app.state.run_event_stream = stream
    return stream


def _durable_snapshot(request: Request) -> DurableRunSnapshot:
    """Return the durable PG snapshot reader, honouring a test/prod override.

    Tests inject ``app.state.run_snapshot``; otherwise one is built over the app
    engine and cached. It is the authoritative source the SSE recovery path reads
    when Redis reports a gap, so a trimmed or flushed stream never silently drops
    a milestone.
    """
    override = getattr(request.app.state, "run_snapshot", None)
    if override is not None:
        return override
    factory = async_sessionmaker(request.app.state.async_engine, expire_on_commit=False)
    snapshot = DurableRunSnapshot(factory=factory)
    request.app.state.run_snapshot = snapshot
    return snapshot


class RunRequest(BaseModel):
    """Body for ``POST /runs``. Tenant/user are never accepted here."""

    kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    priority_lane: str = Field(default="default", max_length=40)
    max_attempts: int = Field(default=5, ge=1, le=50)


def _run_view(job: DurableJob) -> dict[str, Any]:
    return {
        "run_id": job.run_id or job.id,
        "job_id": job.id,
        "tenant_id": job.tenant_id,
        "kind": job.kind,
        "state": job.state,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "idempotency_key": job.idempotency_key,
        "priority_lane": job.priority_lane,
        "created_at": job.created_at.isoformat(),
    }


@router.post("/runs", status_code=201)
async def create_run(
    body: RunRequest,
    principal: PrincipalDep,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    """Enqueue a durable run for the caller's tenant.

    ``400`` when the Idempotency-Key is missing or out of the ``16..128`` range;
    ``429``/``503`` + ``Retry-After`` when admission declines; ``409`` when the
    same key is reused with a different payload; otherwise ``201`` with the run
    view. A repeat with the same key + payload returns the existing run.
    """
    key = (idempotency_key or "").strip()
    if not (IDEMPOTENCY_KEY_MIN <= len(key) <= IDEMPOTENCY_KEY_MAX):
        raise ApplicationError(
            "IDEMPOTENCY_KEY_INVALID",
            f"Idempotency-Key header must be {IDEMPOTENCY_KEY_MIN}-"
            f"{IDEMPOTENCY_KEY_MAX} characters",
            status_code=400,
        )

    identity = f"{principal.tenant_id}:{principal.user_id}"
    outcome = await _admission(request).admit(resource=body.kind, identity=identity)
    if not outcome.admitted:
        retry_after = max(1, math.ceil(outcome.retry_after_seconds))
        return JSONResponse(
            status_code=outcome.http_status,
            headers={"Retry-After": str(retry_after)},
            content={
                "success": False,
                "error": {
                    "code": outcome.error_code,
                    "message": outcome.reason,
                    "details": {"retry_after_seconds": retry_after},
                },
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    service = _job_service(request)
    try:
        job = await service.enqueue(
            tenant_id=principal.tenant_id,
            kind=body.kind,
            payload=body.payload,
            idempotency_key=key,
            max_attempts=body.max_attempts,
            priority_lane=body.priority_lane,
        )
    except JobIdempotencyConflict as exc:
        raise ApplicationError(
            "IDEMPOTENCY_KEY_CONFLICT", str(exc), status_code=409
        ) from exc
    return _run_view(job)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, principal: PrincipalDep, request: Request) -> Any:
    """Read one run the caller's tenant owns. Cross-tenant reads are ``404``."""
    service = _job_service(request)
    job = await service.get(run_id)
    if job is None or job.tenant_id != principal.tenant_id:
        raise NotFoundError("run not found")
    return _run_view(job)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, principal: PrincipalDep, request: Request) -> Any:
    """Request cooperative cancellation of a run the caller's tenant owns.

    Tenant scoping matches :func:`get_run`: an unknown run, or one owned by
    another tenant, is a ``404`` before any transition, so cancellation cannot be
    used to probe another tenant's runs. This is a durable state change -- a
    version-CAS to ``cancel_requested`` plus a ``job.cancel_requested`` outbox
    event in the same transaction (:meth:`JobService.request_cancel`) -- not a
    hard kill: a leased worker observes the flag between steps and stops
    cooperatively (that worker loop is gated on the broker, T064/T072). Repeating
    the request is idempotent; a run already in a terminal state
    (succeeded / failed / cancelled) is a ``409``.
    """
    service = _job_service(request)
    job = await service.get(run_id)
    if job is None or job.tenant_id != principal.tenant_id:
        raise NotFoundError("run not found")
    try:
        job = await service.request_cancel(job_id=job.id)
    except JobStateError as exc:
        raise ApplicationError(
            "RUN_NOT_CANCELLABLE", str(exc), status_code=409
        ) from exc
    return _run_view(job)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    principal: PrincipalDep,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Stream a run's SSE events for the caller's tenant: replay, then close.

    The tenant is authorised the same way as :func:`get_run` -- an unknown run,
    or one owned by another tenant, is a ``404`` before any stream is opened, so
    the event log never leaks across tenants. The response then replays every
    event retained since the client's ``Last-Event-ID``; when the resume point was
    trimmed (or Redis was flushed) it emits ``snapshot_required`` and replays the
    authoritative durable milestones from PostgreSQL, then returns once the backlog
    drains.

    This is the **replay/catch-up** surface, verifiable end-to-end against a real
    Redis with no worker running. The live tail (a producer fanning worker events
    into a per-connection :class:`~backend.app.sse.channel.BoundedEventChannel`)
    is implemented at the generator level but not wired here: it depends on the
    Celery consumers (T064) and is gated on the broker, so this endpoint does not
    pretend to hold a connection open for events that nothing is producing yet.
    """
    service = _job_service(request)
    job = await service.get(run_id)
    if job is None or job.tenant_id != principal.tenant_id:
        raise NotFoundError("run not found")

    stream = _run_event_stream(request)
    public_run_id = job.run_id or job.id
    snapshot = _durable_snapshot(request).bind(
        tenant_id=principal.tenant_id, run_id=public_run_id
    )
    generator = sse_event_source(
        stream=stream,
        run_id=public_run_id,
        last_event_id=last_event_id,
        channel=None,
        snapshot=snapshot,
    )
    return EventSourceResponse(_instrumented_stream(generator))


async def _instrumented_stream(
    generator: AsyncIterator[Mapping[str, Any]],
) -> AsyncIterator[Mapping[str, Any]]:
    """Wrap an SSE frame source so a connection moves the live-SSE gauge.

    The gauge goes ``+1`` when the connection opens and ``-1`` when it unwinds
    (backlog drained, client gone, or error), and the teardown cost is timed
    into the ``sse`` cleanup histogram -- so a leaked connection or a slow
    release is visible rather than silent. Telemetry is a no-op unless a meter
    is configured, so this is safe on the default (unconfigured) path.
    """
    telemetry.record_sse_connection(delta=1)
    opened_at = perf_counter()
    try:
        async for frame in generator:
            yield frame
    finally:
        telemetry.record_sse_connection(delta=-1)
        telemetry.record_cleanup_duration(
            scope="sse", duration_seconds=perf_counter() - opened_at
        )
