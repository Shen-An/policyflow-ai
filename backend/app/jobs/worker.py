"""T064 [US2] Celery worker entrypoint.

Run a real durable-job consumer against the configured broker + database::

    celery -A backend.app.jobs.worker worker --pool=solo -Q policyflow.default

The module builds the configured Celery app, a :class:`WorkerRuntime` over
``DATABASE_URL``, and registers the single consumer task. ``--pool=solo`` is used
on Windows (prefork is unsupported there); on Linux the default prefork pool works
unchanged because the task body drives its async cycle on a per-process loop.

It also registers a built-in ``recovery_probe`` job kind: a tiny, idempotent job
that exercises the full enqueue -> outbox -> broker -> consume -> complete pipeline
end to end. It is a legitimate operational canary (and what the live-broker
recovery suite drives), not a test-only hack -- it runs a real durable job and
reaches a real terminal state, optionally sleeping ``payload["sleep_ms"]`` so a
recovery drill can kill a worker while the job is in flight.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from backend.app.core.config import get_settings
from backend.app.jobs.celery_app import build_celery_app
from backend.app.jobs.consumer import build_worker_runtime, register_job_task
from backend.app.jobs.runner import JobContext, default_registry

#: Built-in canary job kind exercising the full durable pipeline end to end.
RECOVERY_PROBE_KIND = "recovery_probe"


async def _handle_recovery_probe(ctx: JobContext, payload: dict[str, Any]) -> str | None:
    """Idempotent canary: optionally sleep, then return a pid-stamped result_ref.

    The pid in the result lets a recovery drill prove *which* worker completed the
    job (e.g. a second worker after the first was killed). The sleep window lets a
    drill kill the worker mid-flight.
    """
    sleep_ms = int(payload.get("sleep_ms", 0) or 0)
    if sleep_ms > 0:
        await asyncio.sleep(sleep_ms / 1000.0)
    return f"probe-ok:pid={os.getpid()}"


def build_registry():
    """Default handler registry plus the recovery-probe canary."""
    registry = default_registry()
    registry.register(RECOVERY_PROBE_KIND, _handle_recovery_probe)
    return registry


def build_app():
    """Build the Celery app + runtime + registered task from the environment."""
    settings = get_settings()
    app = build_celery_app(settings)
    runtime = build_worker_runtime(
        database_url=settings.DATABASE_URL,
        registry=build_registry(),
        worker_id=f"celery-worker:{os.getpid()}",
        lease_seconds=int(os.environ.get("POLICYFLOW_WORKER_LEASE_SECONDS", "15")),
        settings=settings,
    )
    register_job_task(app, runtime)
    return app, runtime


# Celery's CLI imports ``celery_app`` from ``-A backend.app.jobs.worker``.
celery_app, _runtime = build_app()
