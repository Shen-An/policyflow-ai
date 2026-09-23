"""Route adapter contract (T051/T052).

Proves the live-endpoint GraphRouteAdapter records the Stage 9 removal-ledger
telemetry and delegates to the shared pipeline-graph path unchanged. Uses
monkeypatched service functions so the contract is verified deterministically
without a running retrieval stack.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.graph.compat import AdapterUsageTelemetry
from backend.app.graph.route_adapter import GraphRouteAdapter


@pytest.mark.asyncio
async def test_chat_delegates_and_records(monkeypatch):
    telemetry = AdapterUsageTelemetry()
    adapter = GraphRouteAdapter(telemetry)
    captured = {}

    async def fake_send(session, user, data, pipeline, **deps):
        captured["deps"] = deps
        return SimpleNamespace(query_log_id="q-1", answer="ok")

    monkeypatch.setattr(
        "backend.app.services.chat_service.send_chat_message", fake_send
    )
    response = await adapter.chat(
        session=object(),
        user=SimpleNamespace(tenant_id="t-1"),
        data=object(),
        pipeline=object(),
        rag_service="rag",
    )
    assert response.query_log_id == "q-1"
    assert captured["deps"] == {"rag_service": "rag"}
    assert telemetry.usage_count("route_chat") == 1
    event = telemetry.events[-1]
    assert event.tenant_id == "t-1" and event.run_id == "q-1"
    # Telemetry must never carry payload content.
    assert "ok" not in repr(event) and "q-1" in repr(event)


@pytest.mark.asyncio
async def test_chat_events_records_once_and_streams(monkeypatch):
    telemetry = AdapterUsageTelemetry()
    adapter = GraphRouteAdapter(telemetry)

    async def fake_iter(session, user, data, pipeline, **deps):
        yield ("stage", {"name": "retrieve"})
        yield ("final", {"answer": "ok"})

    monkeypatch.setattr(
        "backend.app.services.chat_service.iter_chat_events", fake_iter
    )
    events = [
        ev
        async for ev in adapter.chat_events(
            session=object(),
            user=SimpleNamespace(tenant_id="t-2"),
            data=object(),
            pipeline=object(),
        )
    ]
    assert [name for name, _ in events] == ["stage", "final"]
    assert telemetry.usage_count("route_chat_stream") == 1


@pytest.mark.asyncio
async def test_run_eval_delegates_and_records(monkeypatch):
    telemetry = AdapterUsageTelemetry()
    adapter = GraphRouteAdapter(telemetry)
    called = {}

    async def fake_exec(engine, rag_service, pipeline, run_id, data):
        called["run_id"] = run_id

    monkeypatch.setattr(
        "backend.app.services.eval_service.execute_eval_run", fake_exec
    )
    await adapter.run_eval(
        engine=object(),
        rag_service=object(),
        pipeline=object(),
        run_id="run-9",
        data=object(),
        tenant_id="t-3",
    )
    assert called["run_id"] == "run-9"
    assert telemetry.usage_count("route_eval") == 1


def test_no_telemetry_is_safe():
    # Adapter with no telemetry sink must not raise when recording.
    GraphRouteAdapter(None)._record("route_chat", "t", "r")
