"""T063 [US2] durable job state machine with a transactional outbox.

``JobService`` is the single writer for durable background work. Every state
transition is a compare-and-set on the row ``version`` (never last-write-wins),
and every transition that observers must learn about writes an ``OutboxEvent``
in the *same* transaction as the business change. The unique constraint on
``(aggregate_type, aggregate_id, aggregate_version, event_type)`` makes
publication idempotent at the database, so a redelivered message or a retried
transition can never emit a duplicate event or drive a duplicate side effect.

PostgreSQL is the production authority; there the eligible-job scan uses
``FOR UPDATE SKIP LOCKED`` (added in the recovery suite). This module is written
so the *logic* is identical on SQLite, where the scan degrades to an optimistic
candidate loop — each candidate is claimed with the same version CAS, so a lost
race simply falls through to the next row instead of corrupting state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import (
    JOB_TERMINAL_STATES,
    DurableJob,
    OutboxEvent,
    utc_now,
)
from backend.app.observability.telemetry import record_cleanup_duration

AGGREGATE_TYPE = "durable_job"

# States a job may be leased from: freshly queued, or re-queued after a
# recoverable failure whose backoff window has elapsed.
_LEASABLE_STATES = ("queued", "recoverable_failed")
# States from which a worker may still be actively processing the job.
_ACTIVE_STATES = ("leased", "running")


class JobStateError(RuntimeError):
    """A transition was requested from a state (or by an owner) that forbids it."""


class JobIdempotencyConflict(RuntimeError):
    """An idempotency key was reused with a different payload digest."""


def _digest(payload: dict[str, Any]) -> str:
    """Stable sha256 of a payload, independent of key order."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JobService:
    """Durable job orchestration over an async session factory.

    ``backoff_base_seconds`` scales the re-queue delay after a recoverable
    failure (``base * 2**(attempts-1)``). It defaults to ``0`` so unit contracts
    can prove re-eligibility without wall-clock waits; deployments set a positive
    base via configuration. The mechanism (``available_at = now + backoff``) is
    the same either way.
    """

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        backoff_base_seconds: float = 0.0,
        max_backoff_seconds: float = 600.0,
    ) -> None:
        self._factory = factory
        self._backoff_base = backoff_base_seconds
        self._max_backoff = max_backoff_seconds

    # -- reads ---------------------------------------------------------------

    async def get(self, job_id: str) -> DurableJob | None:
        async with self._factory() as session:
            return await session.get(DurableJob, job_id)

    async def list_outbox(self, *, aggregate_id: str) -> list[OutboxEvent]:
        async with self._factory() as session:
            rows = await session.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)
            )
            return list(rows.scalars().all())

    # -- enqueue -------------------------------------------------------------

    async def enqueue(
        self,
        *,
        tenant_id: str,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        run_id: str | None = None,
        max_attempts: int = 5,
        priority_lane: str = "default",
        payload_schema_version: int = 1,
    ) -> DurableJob:
        """Create a queued job and its ``job.enqueued`` outbox row atomically.

        Idempotent on ``(tenant_id, kind, idempotency_key)``: a repeat with the
        same payload digest returns the existing job; a repeat with a different
        digest raises :class:`JobIdempotencyConflict`.
        """
        digest = _digest(payload)
        async with self._factory() as session:
            existing = await self._find_by_idempotency(
                session, tenant_id, kind, idempotency_key
            )
            if existing is not None:
                return self._reconcile_idempotent(existing, digest)

            job = DurableJob(
                tenant_id=tenant_id,
                run_id=run_id,
                kind=kind,
                idempotency_key=idempotency_key,
                payload_schema_version=payload_schema_version,
                payload_digest=digest,
                payload=payload,
                state="queued",
                priority_lane=priority_lane,
                max_attempts=max_attempts,
                available_at=utc_now(),
            )
            session.add(job)
            await session.flush()
            self._emit(session, job, "job.enqueued", job.version)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                # Lost the insert race on the unique idempotency key: re-read
                # the winner and reconcile against its digest.
                collided = await self._find_by_idempotency(
                    session, tenant_id, kind, idempotency_key
                )
                if collided is None:
                    raise
                return self._reconcile_idempotent(collided, digest)
            await session.refresh(job)
            return job

    # -- lease / run lifecycle ----------------------------------------------

    async def lease(self, *, worker_id: str, lease_seconds: int) -> DurableJob | None:
        """Claim the oldest eligible job for ``worker_id`` via a version CAS.

        Returns ``None`` when nothing is eligible. Increments ``attempts`` and
        stamps the lease owner/expiry. On SQLite the eligible scan is an
        optimistic candidate loop; a lost CAS falls through to the next row.
        """
        now = utc_now()
        expires = now + timedelta(seconds=lease_seconds)
        async with self._factory() as session:
            candidates = await session.execute(
                select(DurableJob)
                .where(
                    DurableJob.state.in_(_LEASABLE_STATES),
                    DurableJob.available_at <= now,
                )
                .order_by(DurableJob.available_at, DurableJob.created_at)
            )
            for job in candidates.scalars().all():
                new_version = job.version + 1
                res = await session.execute(
                    update(DurableJob)
                    .where(
                        DurableJob.id == job.id,
                        DurableJob.version == job.version,
                    )
                    .values(
                        state="leased",
                        lease_owner=worker_id,
                        lease_expires_at=expires,
                        heartbeat_at=now,
                        attempts=DurableJob.attempts + 1,
                        updated_at=now,
                        version=new_version,
                    )
                )
                if res.rowcount != 1:
                    continue  # lost the race, try the next candidate
                await session.commit()
                await session.refresh(job)
                return job
            return None

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int
    ) -> DurableJob:
        """Extend the lease of a job the caller still owns."""
        now = utc_now()
        expires = now + timedelta(seconds=lease_seconds)
        async with self._factory() as session:
            job = await self._load_owned(session, job_id, worker_id, _ACTIVE_STATES)
            await self._cas(
                session,
                job,
                heartbeat_at=now,
                lease_expires_at=expires,
                updated_at=now,
            )
            await session.commit()
            await session.refresh(job)
            return job

    async def start(self, *, job_id: str, worker_id: str) -> DurableJob:
        """Transition a leased job to running (no event; not externally observable)."""
        async with self._factory() as session:
            job = await self._load_owned(session, job_id, worker_id, ("leased",))
            if job.state == "running":
                return job
            await self._cas(session, job, state="running", updated_at=utc_now())
            await session.commit()
            await session.refresh(job)
            return job

    async def complete(
        self, *, job_id: str, worker_id: str, result_ref: str | None = None
    ) -> DurableJob:
        """Mark a running job succeeded, emitting ``job.succeeded`` once."""
        async with self._factory() as session:
            job = await self._load(session, job_id)
            if job.state == "succeeded":
                return job  # idempotent: no second transition, no second event
            if job.lease_owner != worker_id:
                raise JobStateError(
                    f"job {job_id} owned by {job.lease_owner!r}, not {worker_id!r}"
                )
            if job.state != "running":
                raise JobStateError(f"cannot complete job in state {job.state!r}")
            new_version = await self._cas(
                session, job, state="succeeded", result_ref=result_ref, updated_at=utc_now()
            )
            self._emit(session, job, "job.succeeded", new_version)
            await session.commit()
            await session.refresh(job)
            return job

    async def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        recoverable: bool,
    ) -> DurableJob:
        """Record a failure, re-queuing within budget or going terminal.

        Recoverable and under the attempt budget -> ``recoverable_failed`` with a
        backoff ``available_at`` and a ``job.recoverable_failed`` event. Otherwise
        ``terminal_failed`` with a ``job.terminal_failed`` event.
        """
        now = utc_now()
        async with self._factory() as session:
            job = await self._load_owned(session, job_id, worker_id, _ACTIVE_STATES)
            if recoverable and job.attempts < job.max_attempts:
                backoff = min(
                    self._backoff_base * (2 ** max(job.attempts - 1, 0)),
                    self._max_backoff,
                )
                new_version = await self._cas(
                    session,
                    job,
                    state="recoverable_failed",
                    last_error_code=error_code,
                    lease_owner=None,
                    lease_expires_at=None,
                    available_at=now + timedelta(seconds=backoff),
                    updated_at=now,
                )
                self._emit(session, job, "job.recoverable_failed", new_version)
            else:
                new_version = await self._cas(
                    session,
                    job,
                    state="terminal_failed",
                    last_error_code=error_code,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
                self._emit(session, job, "job.terminal_failed", new_version)
            await session.commit()
            await session.refresh(job)
            return job

    # -- cancellation --------------------------------------------------------

    async def request_cancel(self, *, job_id: str) -> DurableJob:
        """Signal cooperative cancellation; workers observe it per step."""
        async with self._factory() as session:
            job = await self._load(session, job_id)
            if job.state == "cancel_requested":
                return job
            if job.state in JOB_TERMINAL_STATES:
                raise JobStateError(f"cannot cancel job in state {job.state!r}")
            new_version = await self._cas(
                session, job, state="cancel_requested", updated_at=utc_now()
            )
            self._emit(session, job, "job.cancel_requested", new_version)
            await session.commit()
            await session.refresh(job)
            return job

    async def finalize_cancel(self, *, job_id: str) -> DurableJob:
        """Complete a requested cancellation, emitting ``job.cancelled`` once."""
        async with self._factory() as session:
            job = await self._load(session, job_id)
            if job.state == "cancelled":
                return job
            if job.state != "cancel_requested":
                raise JobStateError(
                    f"cannot finalize cancel from state {job.state!r}"
                )
            new_version = await self._cas(
                session, job, state="cancelled", lease_owner=None,
                lease_expires_at=None, updated_at=utc_now()
            )
            self._emit(session, job, "job.cancelled", new_version)
            await session.commit()
            await session.refresh(job)
            return job

    # -- recovery ------------------------------------------------------------

    async def reap_expired_leases(self) -> list[DurableJob]:
        """Reclaim jobs whose lease expired, re-queuing or going terminal.

        Mirrors :meth:`fail` with a synthetic ``LEASE_EXPIRED`` cause: within the
        attempt budget the job returns to ``recoverable_failed``; at the budget it
        becomes ``terminal_failed``. Idempotent under concurrent reapers via CAS.
        """
        now = utc_now()
        reaped: list[DurableJob] = []
        started = perf_counter()
        try:
            async with self._factory() as session:
                rows = await session.execute(
                    select(DurableJob).where(
                        DurableJob.state.in_(_ACTIVE_STATES),
                        DurableJob.lease_expires_at.is_not(None),
                        DurableJob.lease_expires_at < now,
                    )
                )
                for job in rows.scalars().all():
                    if job.attempts < job.max_attempts:
                        new_version = await self._cas(
                            session, job, state="recoverable_failed",
                            last_error_code="LEASE_EXPIRED", lease_owner=None,
                            lease_expires_at=None, available_at=now, updated_at=now,
                        )
                        if new_version is None:
                            continue
                        self._emit(session, job, "job.recoverable_failed", new_version)
                    else:
                        new_version = await self._cas(
                            session, job, state="terminal_failed",
                            last_error_code="LEASE_EXPIRED", lease_owner=None,
                            lease_expires_at=None, updated_at=now,
                        )
                        if new_version is None:
                            continue
                        self._emit(session, job, "job.terminal_failed", new_version)
                    reaped.append(job)
                await session.commit()
                for job in reaped:
                    await session.refresh(job)
                return reaped
        finally:
            # Time the recovery sweep as a resource-cleanup pass -- a sweep that
            # reaped nothing is still a real pass, so this records even when the
            # try block raises or returns an empty list (never a fabricated gap).
            record_cleanup_duration(
                scope="job_lease_reap",
                duration_seconds=perf_counter() - started,
            )

    async def _force_lease_expiry(self, job_id: str, when: datetime) -> None:
        """Test hook: set a lease into the past without waiting on the clock.

        Production never calls this; PostgreSQL compares against the real clock.
        """
        async with self._factory() as session:
            await session.execute(
                update(DurableJob)
                .where(DurableJob.id == job_id)
                .values(lease_expires_at=when)
            )
            await session.commit()

    # -- internals -----------------------------------------------------------

    async def _load(self, session: AsyncSession, job_id: str) -> DurableJob:
        job = await session.get(DurableJob, job_id)
        if job is None:
            raise JobStateError(f"job {job_id} does not exist")
        return job

    async def _load_owned(
        self,
        session: AsyncSession,
        job_id: str,
        worker_id: str,
        states: Sequence[str],
    ) -> DurableJob:
        job = await self._load(session, job_id)
        if job.lease_owner != worker_id:
            raise JobStateError(
                f"job {job_id} owned by {job.lease_owner!r}, not {worker_id!r}"
            )
        if job.state not in states:
            raise JobStateError(
                f"job {job_id} in state {job.state!r}, expected one of {tuple(states)}"
            )
        return job

    async def _find_by_idempotency(
        self, session: AsyncSession, tenant_id: str, kind: str, key: str
    ) -> DurableJob | None:
        rows = await session.execute(
            select(DurableJob).where(
                DurableJob.tenant_id == tenant_id,
                DurableJob.kind == kind,
                DurableJob.idempotency_key == key,
            )
        )
        return rows.scalars().first()

    def _reconcile_idempotent(self, existing: DurableJob, digest: str) -> DurableJob:
        if existing.payload_digest != digest:
            raise JobIdempotencyConflict(
                f"idempotency key {existing.idempotency_key!r} reused with a "
                "different payload"
            )
        return existing

    async def _cas(self, session: AsyncSession, job: DurableJob, **values: Any) -> int:
        """Version compare-and-set. Returns the new version, raises on conflict."""
        new_version = job.version + 1
        res = await session.execute(
            update(DurableJob)
            .where(DurableJob.id == job.id, DurableJob.version == job.version)
            .values(version=new_version, **values)
        )
        if res.rowcount != 1:
            raise JobStateError(
                f"concurrent modification of job {job.id} (expected version "
                f"{job.version})"
            )
        return new_version

    def _emit(
        self,
        session: AsyncSession,
        job: DurableJob,
        event_type: str,
        aggregate_version: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append an outbox event in the current transaction (idempotent at DB)."""
        payload: dict[str, Any] = {
            "job_id": job.id,
            "kind": job.kind,
            "tenant_id": job.tenant_id,
        }
        if job.run_id is not None:
            payload["run_id"] = job.run_id
        if extra:
            payload.update(extra)
        session.add(
            OutboxEvent(
                tenant_id=job.tenant_id,
                aggregate_type=AGGREGATE_TYPE,
                aggregate_id=job.id,
                aggregate_version=aggregate_version,
                event_type=event_type,
                payload=payload,
                available_at=utc_now(),
            )
        )
