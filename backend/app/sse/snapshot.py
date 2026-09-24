"""T067 [US2] durable PostgreSQL snapshot behind the Redis SSE replay.

:class:`~backend.app.sse.stream.RunEventStream` is the fast replay path, but it
is bounded and TTL'd. When a client's resume point has been trimmed -- or Redis
was flushed / restarted -- the stream reports a *gap* it can no longer fill.
The authority for a run's history is PostgreSQL: graph execution appends an
append-only :class:`~backend.app.db.models.RunEvent` milestone per stage under a
monotonic ``(run_id, sequence)`` (see :meth:`RunEventRepository.append`). This
module reads those durable milestones back, ordered by sequence, so the recovery
path composed in :func:`~backend.app.sse.endpoint.sse_event_source` can rebuild
authoritative state on a gap instead of only telling the client to reload and
offering nothing to reload from.

The reader is pure PostgreSQL and holds no state between calls: it opens one
short, tenant-scoped unit of work per read, so it is safe to share one instance
across requests and it survives a total Redis flush by construction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.repositories import UnitOfWork
from backend.app.sse.stream import StreamEvent

#: Upper bound on milestones returned in one snapshot read. Runs emit a bounded
#: number of durable milestones (not per-token fanout), so this comfortably
#: covers a whole run while still capping a single query's memory.
DEFAULT_SNAPSHOT_LIMIT = 1000


class DurableRunSnapshot:
    """Read a run's durable milestones from PostgreSQL, ordered by sequence.

    Every read opens its own short transaction on ``factory`` and applies the
    RLS tenant context, mirroring the :class:`~backend.app.quota.ledger.QuotaLedger`
    idiom: no state is held between calls.
    """

    def __init__(self, *, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def milestones(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int = DEFAULT_SNAPSHOT_LIMIT,
    ) -> list[StreamEvent]:
        """Return the run's durable milestones after ``after_sequence`` in order.

        Each milestone becomes a :class:`StreamEvent` whose ``id`` is the durable
        coordinate ``d{sequence}`` (distinct from a Redis ``ms-seq`` id) and whose
        ``data`` is the stored payload with the durable coordinates folded in, so a
        client can order and resume from an authoritative snapshot.
        """
        async with UnitOfWork(factory=self._factory) as uow:
            await uow.set_tenant_context(tenant_id)
            rows = await uow.run_events.list_for_run(
                tenant_id, run_id, after_sequence=after_sequence, limit=limit
            )
        return [self._to_event(row) for row in rows]

    def bind(
        self, *, tenant_id: str, run_id: str, after_sequence: int = 0
    ) -> Callable[[], Awaitable[list[StreamEvent]]]:
        """Return the zero-arg awaitable ``sse_event_source`` calls on a gap.

        Binding tenant and run here keeps :func:`sse_event_source` decoupled from
        PostgreSQL and tenant scoping: it only knows "read the authoritative
        snapshot", not how one is resolved.
        """

        async def _read() -> list[StreamEvent]:
            return await self.milestones(
                tenant_id=tenant_id, run_id=run_id, after_sequence=after_sequence
            )

        return _read

    @staticmethod
    def _to_event(row: object) -> StreamEvent:
        sequence = int(getattr(row, "sequence"))
        payload = getattr(row, "payload", None)
        data = dict(payload) if payload else {}
        data.setdefault("sequence", sequence)
        stage = getattr(row, "stage", "") or ""
        if stage:
            data.setdefault("stage", stage)
        public_status = getattr(row, "public_status", "") or ""
        if public_status:
            data.setdefault("public_status", public_status)
        return StreamEvent(
            id=f"d{sequence}",
            event_type=str(getattr(row, "event_type")),
            data=data,
        )
