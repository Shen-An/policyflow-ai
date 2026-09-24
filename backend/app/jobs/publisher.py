"""T065 [US2] transactional-outbox publisher (relay).

The single writer (:class:`~backend.app.jobs.service.JobService`) records an
``OutboxEvent`` in the *same* transaction as each business change. This relay is
the other half of the pattern: it claims pending rows, publishes them through a
:class:`OutboxTransport`, and marks them ``delivered`` — or, after a bounded
number of failed attempts, ``dead_letter``. The guarantee is *at-least-once*:
across a crash between publish and mark, a row is re-published rather than lost,
and consumers dedup downstream via the unique
``(aggregate_type, aggregate_id, aggregate_version, event_type)`` constraint.

The relay is written so the same logic runs on SQLite (dev/test) and PostgreSQL
(prod). The production transport is RabbitMQ with publisher confirms (wired in
T062, gated on a live broker). :class:`MockTransport` is the test double; per the
honesty red lines it tags every recorded envelope ``status="mock"`` so a mocked
delivery can never be mistaken for a real broker ack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import OutboxEvent, utc_now


class TransportError(RuntimeError):
    """A transport failed to publish; the row stays pending for another attempt."""


class OutboxTransport(Protocol):
    """Publishes one outbox envelope to the broker. Raises on failure."""

    async def publish(self, envelope: dict[str, Any]) -> None: ...


@dataclass
class MockTransport:
    """In-process test transport. Never talks to a real broker.

    ``fail_always`` makes every publish raise; ``fail_times`` makes the first N
    publishes raise and the rest succeed (models a transient outage). Every
    successfully recorded envelope carries ``status="mock"`` so a mocked delivery
    is never mistaken for a real broker ack.
    """

    fail_always: bool = False
    fail_times: int = 0
    published: list[SimpleNamespace] = field(default_factory=list)
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    _failures: int = 0

    async def publish(self, envelope: dict[str, Any]) -> None:
        if self.fail_always or self._failures < self.fail_times:
            self._failures += 1
            raise TransportError("mock transport configured to fail")
        self.published.append(SimpleNamespace(**envelope))
        self.envelopes.append({**envelope, "status": "mock"})


@dataclass(frozen=True)
class _Claim:
    """A row claimed into ``publishing``, carrying the envelope to publish."""

    event_id: str
    envelope: dict[str, Any]


class OutboxPublisher:
    """Claims pending outbox rows, publishes them, and marks their delivery.

    ``max_attempts`` bounds retries before a row is dead-lettered.
    ``backoff_base_seconds`` scales the re-pend delay after a failed publish
    (``base * 2**(attempts-1)``); it defaults to ``0`` so contracts prove
    re-eligibility without wall-clock waits.
    """

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        transport: OutboxTransport,
        batch_size: int = 100,
        max_attempts: int = 8,
        backoff_base_seconds: float = 0.0,
        max_backoff_seconds: float = 600.0,
    ) -> None:
        self._factory = factory
        self._transport = transport
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._max_backoff = max_backoff_seconds

    async def relay_once(self) -> int:
        """Publish one batch of pending rows. Returns the count delivered.

        Each row is first claimed (``pending`` -> ``publishing``) and committed,
        so a crash mid-publish leaves a reclaimable ``publishing`` row rather than
        a lost event. Publication then succeeds (-> ``delivered``) or fails
        (-> ``pending`` within budget, else ``dead_letter``).
        """
        claims = await self._claim_batch()
        delivered = 0
        for claim in claims:
            try:
                await self._transport.publish(claim.envelope)
            except Exception as exc:  # noqa: BLE001 - any transport error re-pends
                await self._record_failure(claim.event_id, exc)
            else:
                await self._mark_delivered(claim.event_id)
                delivered += 1
        return delivered

    async def _claim_batch(self) -> list[_Claim]:
        now = utc_now()
        claims: list[_Claim] = []
        async with self._factory() as session:
            rows = await session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.delivery_state == "pending",
                    OutboxEvent.available_at <= now,
                )
                .order_by(OutboxEvent.created_at)
                .limit(self._batch_size)
            )
            for row in rows.scalars().all():
                claimed = await self._cas(
                    session, row, delivery_state="publishing", updated_at=now
                )
                if not claimed:
                    continue  # lost the race to another relay; skip
                claims.append(_Claim(event_id=row.id, envelope=self._envelope(row)))
            await session.commit()
        return claims

    async def _mark_delivered(self, event_id: str) -> None:
        now = utc_now()
        async with self._factory() as session:
            row = await session.get(OutboxEvent, event_id)
            if row is None or row.delivery_state == "delivered":
                return
            await self._cas(
                session,
                row,
                delivery_state="delivered",
                attempts=row.attempts + 1,
                delivered_at=now,
                updated_at=now,
            )
            await session.commit()

    async def _record_failure(self, event_id: str, exc: Exception) -> None:
        now = utc_now()
        async with self._factory() as session:
            row = await session.get(OutboxEvent, event_id)
            if row is None:
                return
            attempts = row.attempts + 1
            if attempts >= self._max_attempts:
                await self._cas(
                    session,
                    row,
                    delivery_state="dead_letter",
                    attempts=attempts,
                    last_error_code=type(exc).__name__[:80],
                    updated_at=now,
                )
            else:
                backoff = min(
                    self._backoff_base * (2 ** max(attempts - 1, 0)),
                    self._max_backoff,
                )
                await self._cas(
                    session,
                    row,
                    delivery_state="pending",
                    attempts=attempts,
                    last_error_code=type(exc).__name__[:80],
                    available_at=now + timedelta(seconds=backoff),
                    updated_at=now,
                )
            await session.commit()

    @staticmethod
    def _envelope(row: OutboxEvent) -> dict[str, Any]:
        return {
            "event_id": row.id,
            "tenant_id": row.tenant_id,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "aggregate_version": row.aggregate_version,
            "event_type": row.event_type,
            "payload": row.payload,
        }

    @staticmethod
    async def _cas(session: AsyncSession, row: OutboxEvent, **values: Any) -> bool:
        """Version compare-and-set on an outbox row. Returns whether it applied."""
        res = await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == row.id, OutboxEvent.version == row.version)
            .values(version=row.version + 1, **values)
        )
        return res.rowcount == 1
