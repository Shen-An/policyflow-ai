"""T062 [US2] Celery broker topology is configured for safe at-least-once work.

These assertions run against the *built* Celery app's configuration — no broker
connection is made — so they prove the reliability topology deterministically on
any machine, including one without RabbitMQ. The live broker round-trip
(publish/consume/redeliver) is gated on a reachable broker and lives in the
recovery harness (T072); it is not asserted here.
"""

from __future__ import annotations

from backend.app.core.config import Settings
from backend.app.jobs.celery_app import (
    DEFAULT_TASK_SOFT_TIME_LIMIT_SECONDS,
    DEFAULT_TASK_TIME_LIMIT_SECONDS,
    build_celery_app,
)


def _app(**overrides):
    return build_celery_app(Settings(**overrides))


def test_late_ack_and_reject_on_worker_lost() -> None:
    conf = _app().conf
    # A message is acked only after the task returns, and a killed worker's
    # in-flight message is requeued: this is what makes redelivery (T057) happen.
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True


def test_prefetch_is_one() -> None:
    # At most one un-acked message per worker: a slow task cannot hoard a backlog.
    assert _app().conf.worker_prefetch_multiplier == 1


def test_publisher_confirms_enabled() -> None:
    # The producer waits for a broker ack per message; without this the outbox
    # relay (T065) could believe it published a message the broker dropped.
    assert _app().conf.broker_transport_options.get("confirm_publish") is True


def test_default_queue_is_quorum_with_dlq_routing() -> None:
    conf = _app().conf
    queues = {q.name: q for q in conf.task_queues}
    default = queues[conf.task_default_queue]
    args = default.queue_arguments
    assert args["x-queue-type"] == "quorum"
    assert args["x-dead-letter-routing-key"] == Settings().RABBITMQ_DEAD_LETTER_QUEUE
    # The dead-letter queue itself is declared and is also a quorum queue.
    dlq = queues[Settings().RABBITMQ_DEAD_LETTER_QUEUE]
    assert dlq.queue_arguments["x-queue-type"] == "quorum"


def test_time_limits_present_and_soft_below_hard() -> None:
    conf = _app().conf
    assert conf.task_soft_time_limit == DEFAULT_TASK_SOFT_TIME_LIMIT_SECONDS
    assert conf.task_time_limit == DEFAULT_TASK_TIME_LIMIT_SECONDS
    # Soft must trip before hard so a task gets a chance to clean up.
    assert conf.task_soft_time_limit < conf.task_time_limit


def test_unset_broker_falls_back_to_memory_without_connecting() -> None:
    # With no broker URL the app must still *build* (introspection), defaulting to
    # an in-memory transport rather than attempting a live connection.
    assert _app(CELERY_BROKER_URL=None).conf.broker_url == "memory://"


def test_configured_broker_url_is_used() -> None:
    app = _app(CELERY_BROKER_URL="amqp://guest:guest@localhost:5672//")
    assert app.conf.broker_url.startswith("amqp://")
