"""Compatibility adapter usage telemetry drives the Stage 9 removal gate (T051-T053)."""

from __future__ import annotations

import pytest

from backend.app.graph.compat import (
    AdapterUsageTelemetry,
    build_chat_adapter,
    build_eval_adapter,
)
from backend.app.graph.dependencies import DeterministicGraphDependencies
from backend.app.graph.legacy_adapter import LegacyChatRequest, LegacyEvalRequest
from backend.app.graph.service import GraphService


def _graph() -> GraphService:
    return GraphService(dependencies=DeterministicGraphDependencies(tenant_id="tenant-a"))


@pytest.mark.asyncio
async def test_zero_use_is_the_removal_gate_until_a_legacy_call_happens() -> None:
    telemetry = AdapterUsageTelemetry()
    assert telemetry.zero_use_over_window() is True

    adapter = build_chat_adapter(_graph(), telemetry)
    await adapter.chat(
        LegacyChatRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            message="Can I claim approved travel expenses?",
        )
    )

    assert telemetry.usage_count("legacy_chat") == 1
    assert telemetry.zero_use_over_window() is False


@pytest.mark.asyncio
async def test_chat_and_eval_share_one_graph_and_telemetry_carries_no_content() -> None:
    telemetry = AdapterUsageTelemetry()
    graph = _graph()
    chat = build_chat_adapter(graph, telemetry)
    ev = build_eval_adapter(graph, telemetry)

    await chat.chat(
        LegacyChatRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="c1",
            message="confidential travel question",
        )
    )
    await ev.eval(
        LegacyEvalRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            eval_case_id="case-a",
            question="confidential eval question",
        )
    )

    assert telemetry.usage_count("legacy_chat") == 1
    assert telemetry.usage_count("legacy_eval") == 1
    blob = repr(telemetry.events)
    assert "confidential travel question" not in blob
    assert "confidential eval question" not in blob
