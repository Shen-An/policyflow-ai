"""Failing contracts for the temporary legacy graph adapter (T042)."""

from __future__ import annotations

import pytest

from backend.app.graph.legacy_adapter import (
    AdapterMode,
    LegacyChatRequest,
    LegacyEvalRequest,
    LegacyGraphAdapter,
    RecordingAdapterTelemetry,
)
from backend.app.graph.testing import RecordingGraphService


@pytest.mark.asyncio
async def test_legacy_chat_response_maps_from_shared_graph_result() -> None:
    graph = RecordingGraphService(answer="Travel expenses require approval.")
    telemetry = RecordingAdapterTelemetry()
    adapter = LegacyGraphAdapter(graph=graph, telemetry=telemetry)

    response = await adapter.chat(
        LegacyChatRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            message="What is the travel policy?",
        )
    )

    assert response.answer == "Travel expenses require approval."
    assert response.conversation_id == "conversation-a"
    assert response.run_id == graph.last_result.run_id
    assert response.evidence == graph.last_result.evidence
    assert graph.calls[0].entrypoint == "chat"


@pytest.mark.asyncio
async def test_legacy_eval_response_maps_deterministic_graph_decision() -> None:
    graph = RecordingGraphService(
        answer="Insufficient evidence.",
        evidence_gate="insufficient_evidence",
    )
    adapter = LegacyGraphAdapter(graph=graph, telemetry=RecordingAdapterTelemetry())

    response = await adapter.eval(
        LegacyEvalRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            eval_case_id="case-a",
            question="May this claim be reimbursed?",
        )
    )

    assert response.eval_case_id == "case-a"
    assert response.answer == "Insufficient evidence."
    assert response.evidence_gate == "insufficient_evidence"
    assert graph.calls[0].entrypoint == "eval"
    assert graph.calls[0].deterministic is True


@pytest.mark.asyncio
async def test_shadow_mode_disables_every_side_effect_capability() -> None:
    graph = RecordingGraphService(answer="shadow answer")
    adapter = LegacyGraphAdapter(
        graph=graph,
        telemetry=RecordingAdapterTelemetry(),
        mode=AdapterMode.SHADOW,
    )

    await adapter.chat(
        LegacyChatRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            message="Draft and submit this expense claim.",
        )
    )

    request = graph.calls[0]
    assert request.allow_tools is False
    assert request.allow_writeback is False
    assert request.allow_files is False
    assert request.allow_connectors is False
    assert graph.tool_calls == []
    assert graph.writebacks == []
    assert graph.file_operations == []
    assert graph.connector_calls == []


@pytest.mark.asyncio
async def test_adapter_emits_compatibility_telemetry_without_payload_content() -> None:
    graph = RecordingGraphService(answer="mapped answer")
    telemetry = RecordingAdapterTelemetry()
    adapter = LegacyGraphAdapter(graph=graph, telemetry=telemetry)

    await adapter.chat(
        LegacyChatRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            message="confidential employee question",
        )
    )

    assert len(telemetry.events) == 1
    event = telemetry.events[0]
    assert event.adapter == "legacy_chat"
    assert event.mode == AdapterMode.ACTIVE
    assert event.tenant_id == "tenant-a"
    assert event.run_id == graph.last_result.run_id
    assert event.parity_match is None
    assert "confidential employee question" not in repr(event)
    assert "mapped answer" not in repr(event)
