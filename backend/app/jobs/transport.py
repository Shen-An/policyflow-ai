"""T065 [US2] real AMQP outbox transport: publishes durable-job nudges to RabbitMQ.

The transactional-outbox relay (:class:`~backend.app.jobs.publisher.OutboxPublisher`)
claims pending rows and hands each envelope to an
:class:`~backend.app.jobs.publisher.OutboxTransport`. In production that transport
is RabbitMQ: this module publishes -- *with publisher confirms* -- a Celery task
message that nudges a worker (T064) to drain the authoritative durable-job row the
envelope names.

The message is only a nudge. The ``DurableJob`` row is the source of truth, so a
redelivered or duplicate nudge is safe: the idempotent version-CAS state machine
makes a second drain a no-op, never a duplicate side effect. Publisher confirms
close the "published but lost" gap the outbox pattern exists to cover -- a publish
the broker never acked raises :class:`TransportError`, leaving the row ``pending``
for another relay pass rather than dropping the work silently.

The synchronous Celery publish is run in a worker thread so the async relay loop
is never blocked.
"""

from __future__ import annotations

import asyncio
from typing import Any

from celery import Celery

from backend.app.jobs.publisher import TransportError

#: Name of the Celery task the worker registers (see ``consumer.py``). The outbox
#: transport sends this task with the durable-job id so a worker drains that row.
CELERY_JOB_TASK_NAME = "policyflow.jobs.run_durable_job"


class CeleryOutboxTransport:
    """Publish outbox envelopes to RabbitMQ as Celery task nudges, with confirms.

    Satisfies the ``OutboxTransport`` protocol (``async def publish``). Construct it
    from the configured Celery app (whose ``broker_transport_options`` already set
    ``confirm_publish=True``), optionally overriding the target queue.
    """

    def __init__(self, celery_app: Celery, *, queue: str | None = None) -> None:
        self._app = celery_app
        self._queue = queue or celery_app.conf.task_default_queue

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Send a run-nudge for the envelope's aggregate (a durable job).

        An envelope without an ``aggregate_id`` is not a durable-job event and is a
        no-op (nothing to nudge a worker about). Any publish failure -- including a
        broker that never acked the publish under publisher confirms -- surfaces as
        :class:`TransportError` so the outbox row stays pending and is retried.
        """
        job_id = envelope.get("aggregate_id")
        if not job_id:
            return
        try:
            await asyncio.to_thread(self._send, str(job_id), envelope)
        except Exception as exc:  # noqa: BLE001 - uniform transport-failure contract
            raise TransportError(f"AMQP publish failed: {exc}") from exc

    def _send(self, job_id: str, envelope: dict[str, Any]) -> None:
        self._app.send_task(
            CELERY_JOB_TASK_NAME,
            args=[job_id],
            kwargs={"event_type": envelope.get("event_type")},
            queue=self._queue,
            routing_key=self._queue,
        )
