"""DB-backed provider for the authoritative durable-job state gauge (T071, R25).

The Stage-4 "stable under large-scale concurrency" story needs an honest queue
depth. The process-local up-down counters in
:mod:`backend.app.observability.telemetry` (``record_job_queue_depth`` /
``record_job_lease``) only sum the deltas one process observes, so in a
multi-instance deployment -- where a durable job is enqueued on one instance and
leased on another -- a delta-summed gauge drifts and never reconciles.

The correct reading is pull-based: an OpenTelemetry *observable gauge* whose
callback asks the database for the absolute ``COUNT(*) GROUP BY state, lane`` at
collection time. The database is the single source of truth, so every instance
reports the same depth and a restart cannot lose or double-count. This module
supplies that count and wires it onto the telemetry gauge.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from backend.app.db.models import DurableJob
from backend.app.observability import telemetry


def job_state_counts(engine: Any) -> list[tuple[str, str, int]]:
    """Return ``(state, lane, count)`` for every populated durable-job bucket.

    One synchronous ``SELECT state, priority_lane, COUNT(*) GROUP BY ...`` against
    ``engine`` -- the authoritative count read at the moment of collection.
    """
    with Session(engine) as session:
        rows = session.exec(
            select(DurableJob.state, DurableJob.priority_lane, func.count()).group_by(
                DurableJob.state, DurableJob.priority_lane
            )
        ).all()
    return [(str(state), str(lane), int(count)) for state, lane, count in rows]


def install_job_state_gauge(engine: Any) -> None:
    """Bind the observable job-state gauge to ``engine``'s authoritative counts.

    Call once after :func:`~backend.app.observability.telemetry.configure_telemetry`
    (e.g. in the app lifespan). The gauge callback then queries this engine on
    every collection; the engine must be the synchronous engine that owns the
    ``durable_jobs`` table.
    """
    telemetry.register_job_state_gauge(lambda: job_state_counts(engine))
