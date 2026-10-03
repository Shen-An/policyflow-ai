"""Integration fixtures that need live infrastructure beyond PostgreSQL.

The ``broker_url`` fixture mirrors the ``pg_url`` pattern in ``tests/conftest.py``:
it hands out a RabbitMQ URL and skips the test cleanly when no broker is
reachable, so the live-broker suites never fail on a machine without RabbitMQ --
they skip, exactly like the PostgreSQL suites do without a server.
"""

from __future__ import annotations

import os

import pytest

#: Env var overriding the broker URL; defaults to the local Docker RabbitMQ the
#: Stage-4 recovery harness brings up (non-guest user, loopback-published 5672).
TEST_BROKER_URL_ENV = "POLICYFLOW_TEST_BROKER_URL"
_DEFAULT_BROKER_URL = "amqp://policyflow:policyflow@localhost:5672//"


def _broker_reachable(url: str) -> bool:
    try:
        from kombu import Connection
    except ImportError:  # pragma: no cover - kombu ships with celery
        return False
    try:
        with Connection(url, connect_timeout=3) as conn:
            conn.ensure_connection(max_retries=1, timeout=3)
        return True
    except Exception:  # noqa: BLE001 - any connect failure means "skip, not fail"
        return False


@pytest.fixture(scope="session")
def broker_url() -> str:
    """Session-wide RabbitMQ URL, skipping cleanly when no broker is running."""
    url = os.environ.get(TEST_BROKER_URL_ENV, _DEFAULT_BROKER_URL)
    if not _broker_reachable(url):
        pytest.skip(
            f"RabbitMQ is not reachable at {url} "
            f"(set {TEST_BROKER_URL_ENV} or start the Stage-4 broker)"
        )
    return url
