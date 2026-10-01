"""T071 [US2] cleanup-duration call-site for the lease-reap recovery sweep.

The ``cleanup.duration`` histogram has had exactly one call-site since R15 (SSE
teardown). The lease-expiry recovery sweep (:meth:`JobService.reap_expired_leases`)
is the other real resource-cleanup pass Phase 4 owns -- it is what reclaims the
resources (durable-job slots, held concurrency leases) tied to a worker that died
or disconnected, which is precisely the "disconnected resources freed" invariant
the Stage-4 goal cares about. This pins that the sweep times itself under
``scope="job_lease_reap"`` so operators can watch how long recovery takes, and
that it does so even when the sweep reaps nothing (a timed no-op is still a
signal, not a fabricated absence).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app.db.models import utc_now
from backend.app.observability import telemetry as tm

pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
WORKER = "worker-a"


@pytest.fixture()
def reader():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=meter)
    try:
        yield reader
    finally:
        tm.reset_telemetry()


def _cleanup_points(reader: InMemoryMetricReader, scope: str) -> list:
    data = reader.get_metrics_data()
    out: list = []
    if data is None:
        return out
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == tm.METRIC_CLEANUP_DURATION:
                    for point in metric.data.data_points:
                        if point.attributes.get("scope") == scope:
                            out.append(point)
    return out


async def test_reap_sweep_times_itself_under_cleanup_scope(jobs, reader) -> None:
    producer = jobs.fresh_service()
    job = await producer.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"},
        idempotency_key="k", max_attempts=3,
    )
    dying = jobs.fresh_service()
    await dying.lease(worker_id=WORKER, lease_seconds=60)
    await dying._force_lease_expiry(job.id, utc_now() - timedelta(seconds=1))

    reaped = await jobs.fresh_service().reap_expired_leases()
    assert [j.id for j in reaped] == [job.id]

    points = _cleanup_points(reader, "job_lease_reap")
    assert len(points) == 1
    assert points[0].count == 1
    assert points[0].sum >= 0.0


async def test_reap_sweep_times_itself_even_when_nothing_expired(jobs, reader) -> None:
    # No expired leases: the sweep still records one timed observation, because a
    # recovery pass that found nothing is a real pass -- not a missing one.
    reaped = await jobs.fresh_service().reap_expired_leases()
    assert reaped == []

    points = _cleanup_points(reader, "job_lease_reap")
    assert len(points) == 1
    assert points[0].count == 1
