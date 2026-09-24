"""T062/T064 [US2] Celery application: quorum queue, confirms, late ack, DLQ.

This module builds the Celery app from :class:`Settings` and declares the broker
topology that makes at-least-once delivery safe under worker kills:

* **quorum queue** (``x-queue-type=quorum``) — replicated, survives a broker node
  loss, so an enqueued job is not lost with one node.
* **publisher confirms** (``confirm_publish``) — the producer only considers a
  message enqueued once the broker acks it, closing the "published but lost" gap
  that the transactional outbox relay (T065) depends on.
* **manual late ack** (``task_acks_late`` + ``task_reject_on_worker_lost``) — a
  message is acked only after the task returns; if the worker is killed mid-task
  it is redelivered. Redelivery is made safe by the idempotent state machine
  (see T057), not by hoping it never happens.
* **prefetch=1** (``worker_prefetch_multiplier``) — a worker holds at most one
  un-acked message, so a slow task cannot hoard a backlog behind it.
* **dead-letter routing** — poison messages that exhaust redelivery land on the
  DLQ instead of looping forever.
* **hard/soft time limits** — a wedged task is killed rather than holding its
  prefetch slot indefinitely.

The topology is fully introspectable without a live broker (see
``tests/contract/test_celery_config.py``); the actual broker round-trip is gated
on RabbitMQ being reachable and is exercised in the recovery harness (T072).
"""

from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue

from backend.app.core.config import Settings, get_settings

#: Task execution ceilings. ``soft`` raises ``SoftTimeLimitExceeded`` inside the
#: task (a chance to clean up / mark recoverable); ``hard`` kills the worker
#: process for the task. Soft must be strictly below hard.
DEFAULT_TASK_SOFT_TIME_LIMIT_SECONDS = 300
DEFAULT_TASK_TIME_LIMIT_SECONDS = 360


def build_job_queue(settings: Settings) -> Queue:
    """Declare the durable-job quorum queue with DLQ routing from settings."""
    name = settings.CELERY_TASK_DEFAULT_QUEUE
    exchange = Exchange(name, type="direct", durable=True)
    return Queue(
        name,
        exchange=exchange,
        routing_key=name,
        durable=True,
        queue_arguments={
            "x-queue-type": settings.RABBITMQ_QUEUE_TYPE,  # quorum
            "x-max-length": settings.RABBITMQ_QUEUE_MAX_LENGTH,
            "x-max-length-bytes": settings.RABBITMQ_QUEUE_MAX_BYTES,
            # Overflow / exhausted redelivery -> dead-letter queue.
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": settings.RABBITMQ_DEAD_LETTER_QUEUE,
            "x-overflow": "reject-publish",
        },
    )


def build_dead_letter_queue(settings: Settings) -> Queue:
    """Declare the dead-letter quorum queue where poison messages land."""
    name = settings.RABBITMQ_DEAD_LETTER_QUEUE
    return Queue(
        name,
        exchange=Exchange(name, type="direct", durable=True),
        routing_key=name,
        durable=True,
        queue_arguments={"x-queue-type": settings.RABBITMQ_QUEUE_TYPE},
    )


def build_celery_app(settings: Settings | None = None) -> Celery:
    """Construct the configured Celery app.

    When ``CELERY_BROKER_URL`` is unset the broker is left as ``memory://`` so the
    app can be *built and introspected* without a broker; it will not connect.
    Deployments must set a real ``amqp://`` (or ``amqps://``) broker URL.
    """
    settings = settings or get_settings()
    broker_url = settings.CELERY_BROKER_URL or "memory://"

    app = Celery("policyflow", broker=broker_url, backend=None)
    job_queue = build_job_queue(settings)

    app.conf.update(
        # -- reliability ---------------------------------------------------
        task_acks_late=settings.CELERY_TASK_ACKS_LATE,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
        task_ignore_result=settings.CELERY_TASK_IGNORE_RESULT,
        # Publisher confirms: the producer waits for a broker ack per message.
        broker_transport_options={"confirm_publish": True},
        broker_connection_timeout=settings.CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS,
        broker_connection_retry_on_startup=True,
        # -- topology ------------------------------------------------------
        task_default_queue=job_queue.name,
        task_default_exchange=job_queue.name,
        task_default_routing_key=job_queue.name,
        task_queues=(job_queue, build_dead_letter_queue(settings)),
        # -- execution ceilings -------------------------------------------
        task_soft_time_limit=DEFAULT_TASK_SOFT_TIME_LIMIT_SECONDS,
        task_time_limit=DEFAULT_TASK_TIME_LIMIT_SECONDS,
        # A task that trips a time limit is treated as a failure, not acked.
        task_acks_on_failure_or_timeout=False,
        # -- serialization -------------------------------------------------
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
    )
    return app
