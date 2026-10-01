"""T071 [US2] authoritative durable-job state gauge (cross-instance-correct).

R21 recorded an honest gap: the process-local ``record_job_queue_depth`` /
``record_job_lease`` up/down counters are only self-consistent inside one
process, but a durable job is enqueued on one instance and leased on another, so
a delta-summed gauge drifts across a multi-instance deployment. The correct
reading is an OpenTelemetry *observable gauge* whose callback queries the
authoritative row count at collection time -- the database is the single source
of truth, so every instance reports the same absolute depth.

These tests pin that property through a real in-memory meter: after
``install_job_state_gauge(engine)``, collecting metrics reports one point per
``(state, lane)`` matching ``SELECT ... COUNT(*) GROUP BY state, priority_lane``,
and -- crucially -- a second collection after the DB changes reports the new
*absolute* counts (pull-based), not an accumulated delta. That is what makes it
safe across instances and restarts.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.db.models import DurableJob, Tenant
from backend.app.observability import telemetry as tm

pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402

TENANT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=meter)
    try:
        yield reader
    finally:
        tm.reset_telemetry()


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        session.add(Tenant(id=TENANT_ID, code="alpha", name="Alpha", status="active"))
        session.commit()
    yield eng
    eng.dispose()


def _seed_job(engine, *, state: str, lane: str = "default", n: int = 1) -> None:
    with Session(engine) as session:
        for i in range(n):
            session.add(
                DurableJob(
                    tenant_id=TENANT_ID,
                    kind="kb_reindex",
                    idempotency_key=f"{state}:{lane}:{i}:{id(object())}",
                    payload_digest="0" * 64,
                    payload={"kb": "hr"},
                    state=state,
                    priority_lane=lane,
                )
            )
        session.commit()


def _state_lane_counts(reader: InMemoryMetricReader) -> dict[tuple[str, str], float]:
    data = reader.get_metrics_data()
    out: dict[tuple[str, str], float] = {}
    if data is None:  # no instrument emitted anything this collection
        return out
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == tm.METRIC_JOB_STATE:
                    for point in metric.data.data_points:
                        key = (point.attributes.get("state"), point.attributes.get("lane"))
                        out[key] = point.value
    return out


def test_gauge_reports_authoritative_counts_by_state_and_lane(
    reader: InMemoryMetricReader, engine
) -> None:
    from backend.app.jobs.metrics import install_job_state_gauge

    _seed_job(engine, state="queued", lane="default", n=3)
    _seed_job(engine, state="queued", lane="bulk", n=2)
    _seed_job(engine, state="leased", lane="default", n=1)
    _seed_job(engine, state="succeeded", lane="default", n=4)

    install_job_state_gauge(engine)

    counts = _state_lane_counts(reader)
    assert counts[("queued", "default")] == 3
    assert counts[("queued", "bulk")] == 2
    assert counts[("leased", "default")] == 1
    assert counts[("succeeded", "default")] == 4


def test_gauge_is_pull_based_absolute_not_delta(
    reader: InMemoryMetricReader, engine
) -> None:
    from backend.app.jobs.metrics import install_job_state_gauge

    _seed_job(engine, state="queued", lane="default", n=2)
    install_job_state_gauge(engine)

    first = _state_lane_counts(reader)
    assert first[("queued", "default")] == 2

    # A worker leases one job: queued drops to 1, leased becomes 1. A delta-summed
    # gauge would wrongly report queued==2 still; the observable gauge re-reads the
    # authoritative DB on the next collection.
    with Session(engine) as session:
        job = session.exec(
            select(DurableJob).where(DurableJob.state == "queued")
        ).first()
        job.state = "leased"
        session.add(job)
        session.commit()

    second = _state_lane_counts(reader)
    assert second[("queued", "default")] == 1
    assert second[("leased", "default")] == 1


def test_job_state_counts_matches_group_by(engine) -> None:
    from backend.app.jobs.metrics import job_state_counts

    _seed_job(engine, state="queued", lane="default", n=2)
    _seed_job(engine, state="running", lane="bulk", n=1)

    counts = {(state, lane): n for state, lane, n in job_state_counts(engine)}
    assert counts == {("queued", "default"): 2, ("running", "bulk"): 1}


def test_reset_telemetry_clears_the_gauge(reader: InMemoryMetricReader, engine) -> None:
    from backend.app.jobs.metrics import install_job_state_gauge

    _seed_job(engine, state="queued", n=1)
    install_job_state_gauge(engine)
    assert _state_lane_counts(reader)  # non-empty before reset

    tm.reset_telemetry()
    # Provider is cleared, so no callback runs; the gauge reports nothing.
    assert _state_lane_counts(reader) == {}
