"""T066 [US2] PostgreSQL authority behind Redis-coordinated quota admission.

The :class:`~backend.app.quota.coordinator.QuotaCoordinator` makes the atomic
per-request decision in Redis; this module is the durable side of the same
story. It has three responsibilities and no cross-instance coordination of its
own -- Redis already owns that:

1. **Policy authority.** :meth:`QuotaLedger.resolve_policy` reads the governing
   :class:`~backend.app.db.models.QuotaPolicy` for a ``(tenant, workload)`` from
   PostgreSQL, most-specific tier first (tenant+workload -> tenant -> global+
   workload -> global), highest active ``version`` within a tier. The numbers it
   returns are what seed the coordinator's token bucket and lease semaphore, so
   limits live in the database rather than at the call site.
2. **Lease audit.** :meth:`record_lease` writes a durable
   :class:`~backend.app.db.models.QuotaLease` row when a Redis slot is taken, and
   :meth:`close_lease` stamps its ``released_at`` / ``outcome`` when the slot is
   returned or its lease expires. Closing is idempotent: a redelivered release
   never rewrites an already-recorded outcome, mirroring the exactly-once
   guarantee the durable-job outbox gives the job side.
3. **Usage writeback.** :meth:`record_usage` appends
   :class:`~backend.app.db.models.UsageRecord` rows (``reserved`` at admission,
   ``actual`` at completion) and never edits them, so an over-reservation that is
   later trued-up cannot corrupt the billed figure.

A lease expiring is recorded here but is *not*, on its own, permission to
duplicate a non-idempotent side effect -- that remains gated by the
DurableJob / idempotency-key machinery.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import QuotaLease, QuotaPolicy, UsageRecord, utc_now

#: Policy resolution tiers, most specific first. Each entry is
#: ``(scope, use_tenant, use_workload)``: whether the tenant/workload of the
#: request must match (rather than being null on the policy row).
_RESOLUTION_TIERS: tuple[tuple[str, bool, bool], ...] = (
    ("tenant", True, True),
    ("tenant", True, False),
    ("global", False, True),
    ("global", False, False),
)


class QuotaLedger:
    """Durable PostgreSQL authority for quota policy, lease audit and usage.

    Every method opens its own short transaction on ``factory``; the ledger holds
    no state between calls, so it is safe to share one instance across requests.
    """

    def __init__(self, *, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    # -- policy authority ----------------------------------------------------

    async def resolve_policy(
        self, *, tenant_id: str | None, workload: str | None
    ) -> QuotaPolicy | None:
        """Return the active policy governing ``(tenant_id, workload)``.

        Tries the tiers in :data:`_RESOLUTION_TIERS` in order and returns the
        highest-``version`` active row at the first tier that matches, or
        ``None`` when no active policy applies.
        """
        async with self._factory() as session:
            for scope, use_tenant, use_workload in _RESOLUTION_TIERS:
                if use_tenant and tenant_id is None:
                    continue
                if use_workload and workload is None:
                    continue
                stmt = select(QuotaPolicy).where(
                    QuotaPolicy.scope == scope,
                    QuotaPolicy.active.is_(True),
                )
                stmt = stmt.where(
                    QuotaPolicy.tenant_id == tenant_id if use_tenant
                    else QuotaPolicy.tenant_id.is_(None)
                )
                stmt = stmt.where(
                    QuotaPolicy.workload == workload if use_workload
                    else QuotaPolicy.workload.is_(None)
                )
                stmt = stmt.order_by(QuotaPolicy.version.desc())
                match = (await session.execute(stmt)).scalars().first()
                if match is not None:
                    return match
            return None

    async def upsert_policy(
        self,
        *,
        scope: str,
        tenant_id: str | None,
        workload: str | None,
        requests_per_window: int,
        window_seconds: int,
        tokens_per_window: int,
        max_concurrency: int,
        queue_admission_limit: int,
        version: int = 1,
        active: bool = True,
    ) -> str:
        """Insert (or replace) one policy version, returning its id.

        Uniqueness is ``(scope, tenant_id, workload, version)``; re-inserting the
        same version replaces its numbers so configuration is declarative.
        """
        async with self._factory() as session:
            existing = (
                await session.execute(
                    select(QuotaPolicy).where(
                        QuotaPolicy.scope == scope,
                        QuotaPolicy.tenant_id == tenant_id
                        if tenant_id is not None
                        else QuotaPolicy.tenant_id.is_(None),
                        QuotaPolicy.workload == workload
                        if workload is not None
                        else QuotaPolicy.workload.is_(None),
                        QuotaPolicy.version == version,
                    )
                )
            ).scalars().first()
            now = utc_now()
            if existing is not None:
                existing.requests_per_window = requests_per_window
                existing.window_seconds = window_seconds
                existing.tokens_per_window = tokens_per_window
                existing.max_concurrency = max_concurrency
                existing.queue_admission_limit = queue_admission_limit
                existing.active = active
                existing.updated_at = now
                await session.commit()
                return existing.id
            policy = QuotaPolicy(
                scope=scope,
                tenant_id=tenant_id,
                workload=workload,
                requests_per_window=requests_per_window,
                window_seconds=window_seconds,
                tokens_per_window=tokens_per_window,
                max_concurrency=max_concurrency,
                queue_admission_limit=queue_admission_limit,
                version=version,
                active=active,
            )
            session.add(policy)
            await session.commit()
            return policy.id

    # -- lease audit ---------------------------------------------------------

    async def record_lease(
        self,
        *,
        tenant_id: str,
        resource: str,
        owner: str,
        expires_at: datetime,
        run_id: str | None = None,
        acquired_at: datetime | None = None,
    ) -> QuotaLease:
        """Persist the durable audit row for a Redis concurrency lease.

        ``owner`` is the Redis lease id so the live slot and its audit row can be
        correlated. The row opens with a null ``released_at`` / ``outcome``.
        """
        async with self._factory() as session:
            lease = QuotaLease(
                tenant_id=tenant_id,
                run_id=run_id,
                owner=owner,
                resource=resource,
                acquired_at=acquired_at or utc_now(),
                expires_at=expires_at,
            )
            session.add(lease)
            await session.commit()
            await session.refresh(lease)
            return lease

    async def close_lease(
        self, *, lease_id: str, outcome: str, released_at: datetime | None = None
    ) -> QuotaLease:
        """Stamp a lease closed exactly once.

        The first close wins: a later close (for example a redelivered release, or
        a reaper racing a normal release) leaves the recorded ``outcome`` and
        ``released_at`` untouched. Raises ``KeyError`` for an unknown id.
        """
        async with self._factory() as session:
            lease = await session.get(QuotaLease, lease_id)
            if lease is None:
                raise KeyError(f"quota lease {lease_id!r} does not exist")
            if lease.released_at is None:
                lease.released_at = released_at or utc_now()
                lease.outcome = outcome
                await session.commit()
                await session.refresh(lease)
            return lease

    async def open_leases(self, *, tenant_id: str) -> list[QuotaLease]:
        """Leases for ``tenant_id`` that have not yet been closed."""
        async with self._factory() as session:
            rows = await session.execute(
                select(QuotaLease)
                .where(
                    QuotaLease.tenant_id == tenant_id,
                    QuotaLease.released_at.is_(None),
                )
                .order_by(QuotaLease.acquired_at)
            )
            return list(rows.scalars().all())

    # -- usage writeback -----------------------------------------------------

    async def record_usage(
        self,
        *,
        tenant_id: str,
        kind: str,
        run_id: str | None = None,
        user_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        requests: int = 0,
        reserved_tokens: int = 0,
        actual_tokens: int = 0,
        latency_ms: int | None = None,
        occurred_at: datetime | None = None,
    ) -> UsageRecord:
        """Append one usage record. Never edits an existing row.

        ``kind`` distinguishes ``reserved`` (booked at admission) from ``actual``
        (measured at completion); they are separate rows so a trued-up actual
        never overwrites the reservation.
        """
        async with self._factory() as session:
            record = UsageRecord(
                tenant_id=tenant_id,
                run_id=run_id,
                user_id=user_id,
                kind=kind,
                provider=provider,
                model=model,
                requests=requests,
                reserved_tokens=reserved_tokens,
                actual_tokens=actual_tokens,
                latency_ms=latency_ms,
                occurred_at=occurred_at or utc_now(),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def usage_totals(
        self, *, tenant_id: str, run_id: str | None = None
    ) -> dict[str, int]:
        """Aggregate usage for a tenant (optionally one run).

        Sums are computed in PostgreSQL. Reserved and actual are reported apart so
        the caller can compare booked against measured without re-reading rows.
        """
        async with self._factory() as session:
            stmt = select(
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.requests), 0),
                func.coalesce(func.sum(UsageRecord.reserved_tokens), 0),
                func.coalesce(func.sum(UsageRecord.actual_tokens), 0),
            ).where(UsageRecord.tenant_id == tenant_id)
            if run_id is not None:
                stmt = stmt.where(UsageRecord.run_id == run_id)
            records, requests, reserved, actual = (await session.execute(stmt)).one()
            return {
                "records": int(records),
                "requests": int(requests),
                "reserved_tokens": int(reserved),
                "actual_tokens": int(actual),
            }
