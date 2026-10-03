"""T065/T070 [US2] background outbox relay loop.

The transactional outbox decouples "the job was durably enqueued" (a row committed
in the same transaction as the ``DurableJob``) from "a worker was told about it"
(a broker message). Something has to carry the committed rows to the broker: that
is the *relay*. :class:`~backend.app.jobs.publisher.OutboxPublisher` does one pass;
this loop runs it continuously so, in a broker-backed deployment, every enqueued
job event is published to RabbitMQ and a worker is nudged.

It is started from the application lifespan only when a broker is configured
(``CELERY_BROKER_URL``). A dev / single-node deployment without a broker does not
run this loop at all -- there, the in-process ``LocalJobRunner`` drain nudge drains
jobs over the identical durable rows. The loop backs off when there is nothing to
publish (so it is not a busy-spin) and on error (so a transient broker blip does
not hammer the broker), and it stops promptly on shutdown via a stop event rather
than waiting out a sleep.
"""

from __future__ import annotations

import asyncio

from backend.app.core.logging import get_logger
from backend.app.jobs.publisher import OutboxPublisher

logger = get_logger(__name__)


class OutboxRelayLoop:
    """Run :meth:`OutboxPublisher.relay_once` in a cancellable background loop."""

    def __init__(
        self,
        publisher: OutboxPublisher,
        *,
        idle_sleep_seconds: float = 1.0,
        error_sleep_seconds: float = 5.0,
    ) -> None:
        self._publisher = publisher
        self._idle_sleep = idle_sleep_seconds
        self._error_sleep = error_sleep_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the loop as a background task (idempotent while running)."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="outbox-relay-loop")

    async def stop(self) -> None:
        """Signal the loop to stop and await its exit."""
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            finally:
                self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                delivered = await self._publisher.relay_once()
            except Exception:  # noqa: BLE001 - a relay must survive a transient blip
                logger.warning("Outbox relay pass failed; backing off", exc_info=True)
                await self._sleep(self._error_sleep)
                continue
            if delivered == 0:
                await self._sleep(self._idle_sleep)

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake immediately if a stop has been requested."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
