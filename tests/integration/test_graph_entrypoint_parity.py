"""Failing contracts for shared graph entrypoint parity (T039)."""

from __future__ import annotations

import pytest

from backend.app.auth.principal import RequestPrincipal
from backend.app.graph.entrypoints import GraphEntrypoint, GraphRequest
from backend.app.graph.service import GraphService


EXPECTED_NODES = (
    "validate",
    "memory_load",
    "rewrite",
    "retrieve",
    "rerank",
    "evidence_gate",
    "plan_or_tool",
    "generate",
    "writeback",
    "finalize",
)


def principal(scopes: frozenset[str]) -> RequestPrincipal:
    return RequestPrincipal(
        tenant_id="tenant-a",
        user_id="user-a",
        membership_id="membership-a",
        roles=frozenset({"employee"}),
        scopes=scopes,
        authorization_version=1,
        session_id="session-a",
        request_id="request-a",
        run_id="run-a",
    )


@pytest.mark.parametrize(
    "entrypoint",
    [
        GraphEntrypoint.CHAT,
        GraphEntrypoint.STREAM,
        GraphEntrypoint.EVAL,
        GraphEntrypoint.FILE_WORKFLOW,
    ],
)
@pytest.mark.asyncio
async def test_entrypoints_share_node_sequence_and_deterministic_decision(entrypoint) -> None:
    service = GraphService.deterministic()
    result = await service.execute(
        GraphRequest(
            entrypoint=entrypoint,
            principal=principal(frozenset({"graph:*"})),
            run_id="run-a",
            input_payload={"query": "travel policy", "knowledge_version": "v1"},
            decision_seed="fixed-seed",
        )
    )
    assert tuple(result.node_sequence) == EXPECTED_NODES
    assert result.decision_fingerprint == "fixed-seed:v1:supported"


@pytest.mark.parametrize(
    ("entrypoint", "scope"),
    [
        (GraphEntrypoint.CHAT, "graph:invoke"),
        (GraphEntrypoint.STREAM, "graph:stream"),
        (GraphEntrypoint.EVAL, "graph:eval"),
        (GraphEntrypoint.FILE_WORKFLOW, "graph:file_workflow"),
    ],
)
@pytest.mark.asyncio
async def test_entrypoints_reject_missing_permission(entrypoint, scope) -> None:
    service = GraphService.deterministic()
    with pytest.raises(PermissionError, match=scope):
        await service.execute(
            GraphRequest(
                entrypoint=entrypoint,
                principal=principal(frozenset()),
                run_id="run-a",
                input_payload={"query": "travel policy"},
                decision_seed="fixed-seed",
            )
        )
