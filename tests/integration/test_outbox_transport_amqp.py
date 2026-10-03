"""T065 [US2] real AMQP outbox transport against a live RabbitMQ broker.

Proves the production transport actually publishes to RabbitMQ (not the in-memory
``MockTransport``): a published envelope lands as a message on the durable quorum
queue, a publish under publisher confirms blocks until the broker acks, and an
envelope that names no aggregate is a no-op. Skips cleanly when no broker is
reachable (see ``broker_url`` in ``conftest.py``).
"""

from __future__ import annotations

import pytest

from backend.app.core.config import Settings
from backend.app.jobs.celery_app import build_celery_app, build_job_queue
from backend.app.jobs.publisher import TransportError
from backend.app.jobs.transport import CeleryOutboxTransport

pytestmark = pytest.mark.asyncio


def _message_count(celery_app, queue_name: str) -> int:
    with celery_app.connection_for_write() as conn:
        _, count, _ = conn.default_channel.queue_declare(queue=queue_name, passive=True)
    return count


def _purge(celery_app, queue_name: str) -> None:
    with celery_app.connection_for_write() as conn:
        build_job_queue(Settings(_env_file=None))(conn.default_channel).declare()
        conn.default_channel.queue_purge(queue_name)


def _envelope(job_id: str | None, *, event_type: str = "job.enqueued") -> dict:
    return {
        "event_id": f"evt-{job_id}",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "aggregate_type": "durable_job",
        "aggregate_id": job_id,
        "aggregate_version": 1,
        "event_type": event_type,
        "payload": {},
    }


async def test_publish_lands_a_message_on_the_live_quorum_queue(broker_url) -> None:
    settings = Settings(CELERY_BROKER_URL=broker_url, _env_file=None)
    app = build_celery_app(settings)
    queue = settings.CELERY_TASK_DEFAULT_QUEUE
    _purge(app, queue)
    transport = CeleryOutboxTransport(app)

    await transport.publish(_envelope("job-a"))
    await transport.publish(_envelope("job-b"))

    assert _message_count(app, queue) == 2
    _purge(app, queue)


async def test_envelope_without_aggregate_id_is_a_noop(broker_url) -> None:
    settings = Settings(CELERY_BROKER_URL=broker_url, _env_file=None)
    app = build_celery_app(settings)
    queue = settings.CELERY_TASK_DEFAULT_QUEUE
    _purge(app, queue)
    transport = CeleryOutboxTransport(app)

    await transport.publish(_envelope(None))

    assert _message_count(app, queue) == 0


async def test_publish_failure_raises_transport_error() -> None:
    # An unreachable broker must surface as TransportError (row stays pending),
    # never a silent drop. Uses a bogus port so no live broker is needed.
    settings = Settings(
        CELERY_BROKER_URL="amqp://guest:guest@127.0.0.1:1/",
        CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS=1.0,
        _env_file=None,
    )
    app = build_celery_app(settings)
    transport = CeleryOutboxTransport(app)
    with pytest.raises(TransportError):
        await transport.publish(_envelope("job-x"))
