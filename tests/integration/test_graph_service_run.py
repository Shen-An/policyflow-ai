"""GraphService.run is the real GraphRunner behind the legacy adapter (T049).

Proves the unified runner drives the shared graph and that the legacy Chat/Eval
adapter maps its result — with GraphService (not the recording double) as the
runner, so chat and eval reach the same graph and the same evidence gate.
"""

from __future__ import annotations

import pytest

from backend.app.graph.dependencies import DeterministicGraphDependencies
from backend.app.graph.legacy_adapter import (
    LegacyChatRequest,
    LegacyEvalRequest,
    LegacyGraphAdapter,
    RecordingAdapterTelemetry,
)
from backend.app.graph.service import GraphRunner, GraphRunRequest, GraphService


def _service() -> GraphService:
    return GraphService(dependencies=DeterministicGraphDependencies(tenant_id="tenant-a"))


@pytest.mark.asyncio
async def test_graph_service_satisfies_runner_protocol_and_runs_shared_graph() -> None:
    service = _service()
    assert isinstance(service, GraphRunner)

    result = await service.run(
        GraphRunRequest(
            entrypoint="chat",
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="",
            input_payload={"message": "Can I claim approved travel expenses?", "knowledge_version": "v1"},
        )
    )
    assert result.run_id
    assert result.evidence_gate == "supported"
    assert result.answer.startswith("Based on policy:")


@pytest.mark.asyncio
async def test_legacy_chat_adapter_over_real_graph_service() -> None:
    adapter = LegacyGraphAdapter(graph=_service(), telemetry=RecordingAdapterTelemetry())
    response = await adapter.chat(
        LegacyChatRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            message="Can I claim approved travel expenses?",
        )
    )
    assert response.conversation_id == "conversation-a"
    assert response.answer.startswith("Based on policy:")
    assert response.run_id


@pytest.mark.asyncio
async def test_legacy_eval_adapter_is_deterministic_over_real_graph_service() -> None:
    telemetry = RecordingAdapterTelemetry()
    adapter = LegacyGraphAdapter(graph=_service(), telemetry=telemetry)
    response = await adapter.eval(
        LegacyEvalRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            eval_case_id="case-a",
            question="Can I claim approved travel expenses?",
        )
    )
    assert response.eval_case_id == "case-a"
    assert response.evidence_gate == "supported"
    assert telemetry.events[0].adapter == "legacy_eval"
