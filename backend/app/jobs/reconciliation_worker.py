"""T144 [US5] scheduled cross-store reconciliation with an audit trail.

The reconciler itself (Stage 5, :mod:`backend.app.storage.reconciliation`) decides
*what* disagrees between PostgreSQL, the object store and Milvus. This module is
the operational wrapper the governance surface needs:

* it enumerates the tenants that are active, so a scan is not a manual per-tenant
  ritual;
* it runs one bounded pass per tenant and never repairs unless repair was
  explicitly enabled -- detection is safe to schedule, repair is a decision;
* it audits every finding transition. Event ids are deterministic on
  ``(issue, state, attempts)``, so re-running a pass re-finds the same open issue
  without appending a duplicate ``audit_events`` row (append is idempotent by
  ``event_id``);
* when a store is unreachable it records that as ``blocked`` and stops the pass
  for that tenant, because "every object is missing" during an outage would be a
  false verdict, exactly as the reconciler's own contract states.

Scheduling is explicit: :meth:`ReconciliationWorker.run_forever` is a loop around
:meth:`run_once` that stops on an ``asyncio.Event``. Production wiring is a
deployment concern (the reconciler needs a reachable object store and Milvus), so
the worker takes them by factory rather than inventing them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import ReconciliationIssue, Tenant
from backend.app.db.repositories import UnitOfWork
from backend.app.storage.reconciliation import (
    CrossStoreReconciler,
    ReconciliationUnavailable,
    SweepReport,
)

__all__ = [
    "ReconciliationCycle",
    "ReconciliationWorker",
    "TRANSITION_AUDIT_ACTIONS",
]

#: Audit action per finding family. The state is appended, e.g.
#: ``reconciliation.issue.detected`` / ``reconciliation.issue.escalated``.
TRANSITION_AUDIT_ACTIONS = {
    "opened": "reconciliation.issue.detected",
    "reopened": "reconciliation.issue.reopened",
    "repaired": "reconciliation.issue.repaired",
    "escalated": "reconciliation.issue.escalated",
}


@dataclass
class ReconciliationCycle:
    """What one scheduled pass did, across all tenants."""

    reports: list[SweepReport] = field(default_factory=list)
    unavailable_tenants: list[str] = field(default_factory=list)
    audited_transitions: int = 0
    audited_summaries: int = 0

    @property
    def findings(self) -> int:
        return sum(report.findings for report in self.reports)


class ReconciliationWorker:
    """Runs one cross-store pass per active tenant, with audited transitions."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        reconciler_factory: Callable[[], CrossStoreReconciler]
        | Callable[[], Awaitable[CrossStoreReconciler]],
        interval_seconds: float = 900.0,
        repair: bool = False,
        max_tenants: int = 1000,
    ) -> None:
        """Bind the worker to a session factory and a reconciler factory.

        ``repair`` defaults to ``False``: a scheduled scan may run often, and
        mutating three stores on a timer must be an explicit deployment choice.
        """
        self._factory = factory
        self._reconciler_factory = reconciler_factory
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._repair = bool(repair)
        self._max_tenants = max(1, int(max_tenants))

    # -- tenant enumeration --------------------------------------------------

    async def active_tenants(self) -> list[str]:
        """Return the tenant ids a scheduled scan should visit.

        Only ``active`` tenants are scanned: a suspended tenant's stores are not
        serving traffic, and a deleting tenant is mid-teardown, so findings there
        would be noise rather than governance.
        """
        async with UnitOfWork(factory=self._factory) as uow:
            rows = (
                await uow.session.execute(
                    select(Tenant.id)
                    .where(Tenant.status == "active")
                    .order_by(Tenant.created_at, Tenant.id)
                    .limit(self._max_tenants)
                )
            ).scalars().all()
            return [str(row) for row in rows]

    async def _reconciler(self) -> CrossStoreReconciler:
        """Resolve the reconciler, allowing a synchronous or async factory."""
        built = self._reconciler_factory()
        if asyncio.iscoroutine(built):
            return await built
        return built

    # -- one pass ------------------------------------------------------------

    async def run_once(
        self, tenant_ids: Sequence[str] | None = None
    ) -> ReconciliationCycle:
        """Run one bounded pass and audit its transitions.

        ``tenant_ids`` is an operator/test override; when omitted the worker
        enumerates the active tenants itself.
        """
        targets = list(tenant_ids) if tenant_ids is not None else await self.active_tenants()
        cycle = ReconciliationCycle()
        reconciler = await self._reconciler()

        for tenant_id in targets:
            try:
                report = await reconciler.sweep(
                    tenant_id=tenant_id, repair=self._repair
                )
            except ReconciliationUnavailable as exc:
                cycle.unavailable_tenants.append(tenant_id)
                await self._audit(
                    tenant_id,
                    action="reconciliation.sweep.unavailable",
                    outcome="blocked",
                    resource_kind="tenant",
                    resource_id=tenant_id,
                    metadata={
                        "reason": type(exc).__name__,
                        "repair": self._repair,
                    },
                )
                continue
            cycle.reports.append(report)
            cycle.audited_transitions += await self._audit_transitions(tenant_id, report)
            cycle.audited_summaries += await self._audit_sweep_summary(tenant_id, report)
        return cycle

    # -- audit ---------------------------------------------------------------

    async def _audit_transitions(
        self, tenant_id: str, report: SweepReport
    ) -> int:
        """Audit each finding transition, idempotently.

        The reconciler reports ids as ``kind:id``. Each is loaded so the audit row
        carries the *current* state and attempt count, and the event id is derived
        from that state: a repeated pass over the same unchanged issue maps to the
        same event id and therefore appends nothing.
        """
        audited = 0
        for bucket, action in TRANSITION_AUDIT_ACTIONS.items():
            for entry in getattr(report, bucket, ()):  # opened/reopened/repaired/escalated
                issue_id = entry.split(":", 1)[1] if ":" in entry else entry
                issue = await self._get_issue(tenant_id, issue_id)
                if issue is None:
                    continue
                outcome = {
                    "repaired": "succeeded",
                    "escalated": "blocked",
                }.get(bucket, "allowed")
                event_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"reconciliation:{tenant_id}:{issue.id}:{issue.state}:{issue.attempts}",
                    )
                )
                created = await self._audit(
                    tenant_id,
                    action=action,
                    outcome=outcome,
                    resource_kind="reconciliation_issue",
                    resource_id=issue.id,
                    reason_code=issue.last_error_code,
                    metadata={
                        "issue_kind": issue.issue_kind,
                        "store_pair": issue.store_pair,
                        "state": issue.state,
                        "attempts": issue.attempts,
                        "severity": issue.severity,
                        "bucket": bucket,
                    },
                    event_id=event_id,
                )
                audited += 1 if created else 0
        return audited

    async def _audit_sweep_summary(self, tenant_id: str, report: SweepReport) -> int:
        """Audit one summary row per pass, keyed by the pass's own finding set."""
        digest = "|".join(
            sorted(
                [*report.opened, *report.reopened, *report.repaired, *report.escalated]
            )
        )
        event_id = str(
            uuid5(NAMESPACE_URL, f"reconciliation-sweep:{tenant_id}:{digest}")
        )
        created = await self._audit(
            tenant_id,
            action="reconciliation.sweep.completed",
            outcome="succeeded",
            resource_kind="tenant",
            resource_id=tenant_id,
            metadata={
                "checked_versions": report.checked_versions,
                "checked_manifests": report.checked_manifests,
                "opened": len(report.opened),
                "reopened": len(report.reopened),
                "repaired": len(report.repaired),
                "escalated": len(report.escalated),
                "repair": self._repair,
            },
            event_id=event_id,
        )
        return 1 if created else 0

    async def _get_issue(
        self, tenant_id: str, issue_id: str
    ) -> ReconciliationIssue | None:
        """Load one finding inside its tenant, or return None."""
        async with UnitOfWork(factory=self._factory) as uow:
            await uow.set_tenant_context(tenant_id)
            return (
                await uow.session.execute(
                    select(ReconciliationIssue).where(
                        ReconciliationIssue.tenant_id == tenant_id,
                        ReconciliationIssue.id == issue_id,
                    )
                )
            ).scalars().first()

    async def _audit(
        self,
        tenant_id: str,
        *,
        action: str,
        outcome: str,
        resource_kind: str,
        resource_id: str,
        metadata: dict[str, object],
        event_id: str | None = None,
        reason_code: str | None = None,
    ) -> bool:
        """Append one audit row; return whether it was newly written."""
        async with UnitOfWork(factory=self._factory) as uow:
            await uow.set_tenant_context(tenant_id)
            _row, created = await uow.audit.append(
                tenant_id,
                event_id=event_id or str(uuid5(NAMESPACE_URL, f"{tenant_id}:{action}:{resource_id}")),
                actor_ref="system:reconciliation-worker",
                action=action,
                outcome=outcome,
                resource_kind=resource_kind,
                resource_id=resource_id,
                reason_code=reason_code,
                redacted_metadata=metadata,
            )
            await uow.commit()
            return created

    # -- schedule ------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Run a pass every ``interval_seconds`` until ``stop`` is set.

        The wait is interruptible, so shutdown does not need to wait out a sleep.
        A pass that raises is *not* swallowed silently: the error propagates to
        the caller's supervisor, because a reconciliation loop that quietly dies
        is indistinguishable from one that finds nothing.
        """
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass
            else:
                return
            await self.run_once()
