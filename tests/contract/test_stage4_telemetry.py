"""T071 [US2] Stage-4 concurrency/durability telemetry is recorded and label-safe.

These assert the new instruments through a real in-memory OpenTelemetry meter
(no exporter, no network): the recording helpers move the gauges, count tokens
and failures, and observe the latency/cleanup histograms. They also prove the
label policy still bites -- an identifier-shaped node name is rejected rather
than exported as high-cardinality, per-user metric data.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.app.observability import telemetry as tm

pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402


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


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    points: list = []
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


def _sum_by(reader: InMemoryMetricReader, name: str, key: str) -> dict[str, float]:
    return {p.attributes.get(key): p.value for p in _points(reader, name)}


def test_sse_gauge_moves_up_and_down(reader: InMemoryMetricReader) -> None:
    tm.record_sse_connection(delta=1)
    tm.record_sse_connection(delta=1)
    tm.record_sse_connection(delta=-1)
    points = _points(reader, tm.METRIC_SSE_ACTIVE)
    assert points and points[0].value == 1


def test_queue_depth_is_tracked_per_lane(reader: InMemoryMetricReader) -> None:
    tm.record_job_queue_depth(lane="default", delta=2)
    tm.record_job_queue_depth(lane="default", delta=-1)
    tm.record_job_queue_depth(lane="bulk", delta=3)
    by_lane = _sum_by(reader, tm.METRIC_JOB_QUEUE_DEPTH, "lane")
    assert by_lane["default"] == 1
    assert by_lane["bulk"] == 3


def test_lease_and_llm_concurrency_gauges(reader: InMemoryMetricReader) -> None:
    tm.record_job_lease(delta=1)
    tm.record_job_lease(delta=1)
    tm.record_job_lease(delta=-1)
    tm.record_llm_concurrency(delta=1)
    assert _points(reader, tm.METRIC_JOB_LEASES_HELD)[0].value == 1
    assert _points(reader, tm.METRIC_LLM_CONCURRENCY)[0].value == 1


def test_llm_tokens_split_by_direction(reader: InMemoryMetricReader) -> None:
    tm.record_llm_tokens(prompt=120, completion=45)
    tm.record_llm_tokens(prompt=30)
    by_dir = _sum_by(reader, tm.METRIC_LLM_TOKENS, "direction")
    assert by_dir["prompt"] == 150
    assert by_dir["completion"] == 45


def test_graph_node_records_latency_and_failure(reader: InMemoryMetricReader) -> None:
    tm.record_graph_node(node="retrieve", duration_seconds=0.2)
    tm.record_graph_node(node="answer", duration_seconds=1.5, failed=True)
    latencies = _points(reader, tm.METRIC_GRAPH_NODE_DURATION)
    assert {p.attributes["node"] for p in latencies} == {"retrieve", "answer"}
    failures = _sum_by(reader, tm.METRIC_GRAPH_NODE_FAILURES, "node")
    assert failures == {"answer": 1}


def test_cleanup_duration_histogram(reader: InMemoryMetricReader) -> None:
    tm.record_cleanup_duration(scope="sse", duration_seconds=0.05)
    points = _points(reader, tm.METRIC_CLEANUP_DURATION)
    assert points and points[0].count == 1
    assert points[0].attributes["scope"] == "sse"


def test_invalid_deltas_are_rejected(reader: InMemoryMetricReader) -> None:
    for bad in (0, 2, -2, True):
        with pytest.raises(tm.TelemetryPolicyError):
            tm.record_sse_connection(delta=bad)  # type: ignore[arg-type]
    with pytest.raises(tm.TelemetryPolicyError):
        tm.record_job_queue_depth(delta=0)
    with pytest.raises(tm.TelemetryPolicyError):
        tm.record_graph_node(node="answer", duration_seconds=-1.0)


def test_identifier_shaped_node_label_is_rejected(reader: InMemoryMetricReader) -> None:
    # A UUID-shaped node name would be a per-request identifier: rejected.
    with pytest.raises(tm.TelemetryPolicyError):
        tm.record_graph_node(
            node="11111111-1111-1111-1111-111111111111", duration_seconds=0.1
        )
