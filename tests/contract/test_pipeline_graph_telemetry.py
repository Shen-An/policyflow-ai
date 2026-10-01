"""T071 [US2] graph-node telemetry call-site: the pipeline graph times its nodes.

The Stage-4 telemetry helpers (T071) were proven in isolation; this wires the
first *graph* call-site and proves it end-to-end. ``build_pipeline_graph`` wraps
each node (``route`` / ``tot`` / ``execute``) so every traversal records the
node's latency into the ``graph.node.duration`` histogram and, on a raising node,
increments the ``graph.node.failures`` counter -- keyed by the stable node name,
never an identifier.

This is process-local by construction (a node runs start-to-finish in one
process), so a histogram/counter is correct here without the cross-instance
caveat that rules out a naive delta *gauge* for queue depth. No agents, LLM or
infra are needed: a minimal fake pipeline exposing the three ``_pnode_*``
coroutines drives the compiled graph, and an in-memory meter reads the points.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from backend.app.graph.pipeline_graph import build_pipeline_graph
from backend.app.observability import telemetry as tm


class _FakePipeline:
    """Exposes only the three node coroutines ``build_pipeline_graph`` binds.

    ``route`` sends the traversal straight to ``execute`` (skipping ToT), and
    ``execute`` either finishes or raises, depending on ``fail_execute``.
    """

    def __init__(self, *, fail_execute: bool = False) -> None:
        self._fail_execute = fail_execute
        self.seen: list[str] = []

    async def _pnode_route(self, state: dict[str, Any]) -> dict[str, Any]:
        self.seen.append("route")
        return {"route_kind": "execute"}

    async def _pnode_tot(self, state: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        self.seen.append("tot")
        return {"route_after_tot": "execute"}

    async def _pnode_execute(self, state: dict[str, Any]) -> dict[str, Any]:
        self.seen.append("execute")
        if self._fail_execute:
            raise RuntimeError("boom in execute")
        return {"result": {"ok": True}}


def _collect(reader: InMemoryMetricReader) -> dict[str, list]:
    seen: dict[str, list] = {}
    data = reader.get_metrics_data()
    if data is None:
        return seen
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                seen.setdefault(metric.name, []).extend(metric.data.data_points)
    return seen


def test_pipeline_graph_records_node_latency() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=provider.get_meter("test"))
    try:
        pipeline = _FakePipeline()
        graph = build_pipeline_graph(pipeline)  # type: ignore[arg-type]
        state = asyncio.run(graph.ainvoke({"question": "hi"}))
        assert state["result"] == {"ok": True}
        assert pipeline.seen == ["route", "execute"]

        seen = _collect(reader)
        duration = seen.get(tm.METRIC_GRAPH_NODE_DURATION, [])
        nodes = {p.attributes.get("node") for p in duration}
        assert {"route", "execute"} <= nodes
        # ToT was not traversed, so it must not have a timing point.
        assert "tot" not in nodes
        # A successful traversal records no failures.
        assert not seen.get(tm.METRIC_GRAPH_NODE_FAILURES, [])
    finally:
        tm.reset_telemetry()


def test_pipeline_graph_records_node_failure() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=provider.get_meter("test"))
    try:
        pipeline = _FakePipeline(fail_execute=True)
        graph = build_pipeline_graph(pipeline)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="boom in execute"):
            asyncio.run(graph.ainvoke({"question": "hi"}))

        seen = _collect(reader)
        failures = seen.get(tm.METRIC_GRAPH_NODE_FAILURES, [])
        failed_nodes = {p.attributes.get("node") for p in failures}
        assert "execute" in failed_nodes
        # The failing node is still timed (the finally clause records latency).
        duration = seen.get(tm.METRIC_GRAPH_NODE_DURATION, [])
        assert "execute" in {p.attributes.get("node") for p in duration}
    finally:
        tm.reset_telemetry()
