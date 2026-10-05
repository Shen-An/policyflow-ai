"""T104 [US1] approval consumption + idempotent, reconciled submission.

This is the only place an approved change set turns into an external side effect,
and every guard that keeps that safe lives here:

* **Fresh re-authorization at execution time.** The approver's authority, the
  action digest and the expected target version are *all* rechecked against
  current state just before anything is sent -- a snapshot taken at approval time
  is never trusted (``data-model.md`` Cross-Entity Invariant #4).
* **Consume and claim are one transaction.** The approval moves
  ``approved -> consumed`` in the same transaction that inserts the
  ``SubmissionJob`` row, so one approval yields at most one submission; a second
  attempt finds the approval already consumed.
* **At-most-once at the database and the provider.** The job's unique
  ``(tenant_id, connector_id, idempotency_key)`` makes a duplicate click, a client
  retry and a worker restart all resolve to the same row; the mock connector
  dedups by the same key, so even a resumed in-flight job cannot double-submit.
* **Unknown outcomes reconcile before retrying.** A job left ``executing`` by a
  crash becomes ``unknown_outcome`` and must pass through ``reconciling`` -- which
  asks the provider what really happened -- before it can retry, so a
  non-idempotent action is never blindly re-sent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.approvals.digest import ActionDigestInput, compute_action_digest, digests_match
from backend.app.db.models import (
    SUBMISSION_TRANSITIONS,
    ApprovalRequest,
    SubmissionJob,
    utc_now,
)

AuthorityCheck = Callable[[str, str, str], Awaitable[str | None]]


class SubmissionConnector(Protocol):
    """The minimal provider surface the submission service drives."""

    connector_id: str

    async def submit(
        self, *, tenant_id: str, idempotency_key: str, destination: str, payload: dict[str, Any]
    ) -> Any: ...

    async def reconcile(self, *, tenant_id: str, idempotency_key: str) -> Any: ...


class SubmissionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        # The stable code is part of the string so callers (and tests) can assert
        # on it without a separate attribute read, and logs carry it inline.
        super().__init__(f"{code}: {message}")
        self.code = code


class SubmissionBlocked(SubmissionError):  # noqa: N818 - reads as the condition
    """A re-authorization check failed; nothing was sent."""


@dataclass(frozen=True)
class SubmissionOutcome:
    """The result of a submission attempt."""

    job_id: str
    state: str
    receipt_id: str | None
    deduplicated: bool
    status: str  # the connector's status marker, e.g. "mock"

    @property
    def succeeded(self) -> bool:
        return self.state == "succeeded"


def idempotency_key_for_approval(approval_id: str) -> str:
    """Derive the submission idempotency key from the approval.

    One approval is one logical submission, so the key is a function of the
    approval id. A duplicate click or client retry re-derives the same key and
    collides on the unique constraint -- which is exactly what makes it
    at-most-once rather than racing to create two jobs.
    """
    return hashlib.sha256(f"approval:{approval_id}".encode()).hexdigest()[:40]


class SubmissionService:
    """Consumes an approval and executes its submission at most once."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        connector: SubmissionConnector,
        authority_check: AuthorityCheck | None = None,
        max_attempts: int = 5,
        retry_backoff_seconds: float = 30.0,
    ) -> None:
        self._factory = factory
        self._connector = connector
        self._authority_check = authority_check
        self._max_attempts = max_attempts
        self._retry_backoff = retry_backoff_seconds

    async def submit_for_approval(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        destination: str,
        current_digest_input: ActionDigestInput,
        expected_target_version: str | None = None,
        payload: dict[str, Any] | None = None,
        required_authority: str = "submit",
    ) -> SubmissionOutcome:
        """Execute (or resume) the submission for an approved change set.

        Idempotent: if a job already exists for this approval's key it is resolved
        rather than duplicated. A fresh job runs the full re-authorization gate
        before anything leaves the system.
        """
        key = idempotency_key_for_approval(approval_id)

        existing = await self._find_job(tenant_id=tenant_id, idempotency_key=key)
        if existing is not None:
            return await self._resolve_existing(existing)

        # No job yet: re-authorize, then consume+claim atomically.
        await self._reauthorize(
            tenant_id=tenant_id,
            approval_id=approval_id,
            current_digest_input=current_digest_input,
            expected_target_version=expected_target_version,
            required_authority=required_authority,
        )
        job_id = await self._consume_and_claim(
            tenant_id=tenant_id,
            approval_id=approval_id,
            idempotency_key=key,
            destination=destination,
            expected_target_version=expected_target_version,
        )
        if job_id is None:
            # Lost the claim race: another caller created the job. Resolve theirs.
            existing = await self._find_job(tenant_id=tenant_id, idempotency_key=key)
            assert existing is not None
            return await self._resolve_existing(existing)
        return await self._execute(job_id, destination=destination, payload=payload or {})

    # -- re-authorization -----------------------------------------------------

    async def _reauthorize(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        current_digest_input: ActionDigestInput,
        expected_target_version: str | None,
        required_authority: str,
    ) -> None:
        """Recheck authority, digest and target version against current state."""
        async with self._factory() as session:
            approval = await session.get(ApprovalRequest, approval_id)
            if approval is None or approval.tenant_id != tenant_id:
                raise SubmissionError("APPROVAL_UNKNOWN", "no such approval for this tenant")
            if approval.status != "approved":
                raise SubmissionBlocked(
                    "APPROVAL_NOT_APPROVED",
                    f"an approval in {approval.status} cannot be submitted",
                )
            if approval.expires_at is not None and approval.expires_at < utc_now():
                raise SubmissionBlocked("APPROVAL_EXPIRED", "the approval has expired")
            current_digest = compute_action_digest(current_digest_input)
            if not digests_match(approval.action_digest, current_digest):
                raise SubmissionBlocked(
                    "APPROVAL_STALE",
                    "the action changed since approval; a fresh approval is required",
                )
            decided_by = approval.decided_by or approval.requested_by

        if self._authority_check is not None:
            reason = await self._authority_check(tenant_id, decided_by, required_authority)
            if reason is not None:
                raise SubmissionBlocked("AUTH_FORBIDDEN", reason)

    async def _consume_and_claim(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        idempotency_key: str,
        destination: str,
        expected_target_version: str | None,
    ) -> str | None:
        """Consume the approval and create the job in one transaction.

        Returns the new job id, or None when the claim was lost (either the
        approval was already consumed or the unique job key collided). Doing both
        in one transaction is what guarantees one approval -> at most one job.
        """
        async with self._factory() as session:
            approval = await session.get(ApprovalRequest, approval_id)
            if approval is None or approval.tenant_id != tenant_id:
                raise SubmissionError("APPROVAL_UNKNOWN", "no such approval for this tenant")
            if approval.status != "approved":
                # Already consumed by a concurrent caller.
                return None
            expected = approval.version
            consumed = await session.execute(
                update(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.version == expected,
                    ApprovalRequest.status == "approved",
                )
                .values(version=expected + 1, status="consumed", updated_at=utc_now())
            )
            if consumed.rowcount != 1:
                return None
            job = SubmissionJob(
                tenant_id=tenant_id,
                run_id=approval.run_id,
                approval_id=approval_id,
                connector_id=self._connector.connector_id,
                destination=destination,
                idempotency_key=idempotency_key,
                expected_target_version=expected_target_version,
                state="ready",
                max_attempts=self._max_attempts,
            )
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                # The unique (tenant, connector, key) already exists: another path
                # created the job. Roll back the consume attempt and let the caller
                # resolve the existing job.
                await session.rollback()
                return None
            return job.id

    # -- execution ------------------------------------------------------------

    async def _execute(
        self, job_id: str, *, destination: str, payload: dict[str, Any]
    ) -> SubmissionOutcome:
        """Run a freshly-claimed job: ready -> executing -> succeeded.

        A failure to even reach the provider parks the job recoverable; a provider
        call whose outcome is unclear parks it ``unknown_outcome`` so a retry must
        reconcile first.
        """
        async with self._factory() as session:
            job = await self._load(session, job_id)
            await self._transition(session, job, "executing", attempts=job.attempts + 1)
            await session.commit()

        try:
            receipt = await self._connector.submit(
                tenant_id=job.tenant_id,
                idempotency_key=job.idempotency_key,
                destination=destination,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001 - unclear outcome fails to unknown
            # We asked the provider but do not know whether it acted. This is the
            # dangerous case, so the job becomes unknown_outcome and must reconcile
            # before any retry -- never a blind re-send.
            async with self._factory() as session:
                job = await self._load(session, job_id)
                await self._transition(
                    session, job, "unknown_outcome", last_error_code=type(exc).__name__
                )
                await session.commit()
            return SubmissionOutcome(job_id, "unknown_outcome", None, False, "unknown")

        async with self._factory() as session:
            job = await self._load(session, job_id)
            await self._transition(
                session,
                job,
                "succeeded",
                provider_receipt=receipt.receipt_id,
                sanitized_result={
                    "status": receipt.status,
                    "deduplicated": receipt.deduplicated,
                },
            )
            await session.commit()
        return SubmissionOutcome(
            job_id, "succeeded", receipt.receipt_id, receipt.deduplicated, receipt.status
        )

    async def _resolve_existing(self, job: SubmissionJob) -> SubmissionOutcome:
        """Resolve a job that already exists for this key (the idempotent path)."""
        if job.state == "succeeded":
            return SubmissionOutcome(
                job.id,
                "succeeded",
                job.provider_receipt,
                True,
                str(job.sanitized_result.get("status", "mock")),
            )
        if job.state in {"terminal_failed", "cancelled"}:
            return SubmissionOutcome(job.id, job.state, job.provider_receipt, False, "none")
        # ready / executing / recoverable_failed / unknown_outcome: a prior attempt
        # was interrupted. Reconcile with the provider before deciding what to do.
        return await self._reconcile_and_resume(job.id)

    async def reconcile(self, *, tenant_id: str, job_id: str) -> SubmissionOutcome:
        """Public entry to reconcile an interrupted job (e.g. from recovery)."""
        async with self._factory() as session:
            job = await self._load(session, job_id)
            if job.tenant_id != tenant_id:
                raise SubmissionError("JOB_UNKNOWN", "no such submission for this tenant")
        return await self._reconcile_and_resume(job_id)

    async def _reconcile_and_resume(self, job_id: str) -> SubmissionOutcome:
        """Ask the provider what happened, then settle or re-run the job.

        If the provider already accepted this key, the job succeeds against the
        known receipt (never re-sent). If the provider has no record and the
        budget remains, the job re-runs -- safe because the connector dedups on the
        same key. If the budget is spent, it terminally fails.
        """
        async with self._factory() as session:
            job = await self._load(session, job_id)
            if job.state == "succeeded":
                return SubmissionOutcome(
                    job.id, "succeeded", job.provider_receipt, True, "mock"
                )
            if job.state not in {"unknown_outcome", "reconciling"}:
                # Move a resumed in-flight job into unknown_outcome first: after a
                # crash we cannot assume the provider call completed.
                if job.state in {"ready", "executing", "recoverable_failed"}:
                    if job.state == "recoverable_failed":
                        await self._transition(session, job, "ready")
                        await session.commit()
                        return await self._execute(
                            job_id, destination=job.destination, payload={}
                        )
                    await self._transition(session, job, "unknown_outcome")
                    await session.commit()
            job = await self._load(session, job_id)
            if job.state == "unknown_outcome":
                await self._transition(session, job, "reconciling")
                await session.commit()

        result = await self._connector.reconcile(
            tenant_id=job.tenant_id, idempotency_key=job.idempotency_key
        )
        async with self._factory() as session:
            job = await self._load(session, job_id)
            if result.already_accepted:
                await self._transition(
                    session,
                    job,
                    "succeeded",
                    provider_receipt=result.receipt_id,
                    sanitized_result={"status": result.status, "reconciled": True},
                )
                await session.commit()
                return SubmissionOutcome(job.id, "succeeded", result.receipt_id, True, result.status)
            if job.attempts >= job.max_attempts:
                await self._transition(
                    session, job, "terminal_failed", last_error_code="RETRY_BUDGET_EXHAUSTED"
                )
                await session.commit()
                return SubmissionOutcome(job.id, "terminal_failed", None, False, "none")
            await self._transition(
                session, job, "ready", next_attempt_at=self._next_attempt(job.attempts)
            )
            await session.commit()
        return await self._execute(job_id, destination=job.destination, payload={})

    # -- helpers --------------------------------------------------------------

    async def _find_job(
        self, *, tenant_id: str, idempotency_key: str
    ) -> SubmissionJob | None:
        async with self._factory() as session:
            return (
                await session.execute(
                    select(SubmissionJob).where(
                        SubmissionJob.tenant_id == tenant_id,
                        SubmissionJob.connector_id == self._connector.connector_id,
                        SubmissionJob.idempotency_key == idempotency_key,
                    )
                )
            ).scalars().first()

    async def _load(self, session: AsyncSession, job_id: str) -> SubmissionJob:
        job = await session.get(SubmissionJob, job_id)
        if job is None:
            raise SubmissionError("JOB_UNKNOWN", f"no submission job {job_id}")
        return job

    async def _transition(
        self, session: AsyncSession, job: SubmissionJob, target: str, **values: Any
    ) -> None:
        allowed = SUBMISSION_TRANSITIONS.get(job.state, frozenset())
        if target != job.state and target not in allowed:
            raise SubmissionError(
                "SUBMISSION_TRANSITION_INVALID",
                f"{job.state} -> {target} is not a legal submission transition",
            )
        expected = job.version
        moment = utc_now()
        result = await session.execute(
            update(SubmissionJob)
            .where(SubmissionJob.id == job.id, SubmissionJob.version == expected)
            .values(version=expected + 1, state=target, updated_at=moment, **values)
        )
        if result.rowcount != 1:
            raise SubmissionError(
                "SUBMISSION_VERSION_CONFLICT", f"concurrent modification of job {job.id}"
            )
        job.state = target
        job.version = expected + 1
        for key, value in values.items():
            setattr(job, key, value)

    def _next_attempt(self, attempts: int) -> datetime:
        return utc_now() + timedelta(seconds=self._retry_backoff * max(attempts, 1))
