"""Failing contract tests for the versioned AgentRunState@1 state (T037)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.graph.state import (
    AgentRunState,
    GraphError,
    GraphStatus,
    NodeName,
    RetryBudget,
    ToolBudget,
)


def make_state() -> AgentRunState:
    now = datetime.now(UTC)
    return AgentRunState(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        status=GraphStatus.CREATED,
        current_node=NodeName.VALIDATE,
        deadline_at=now + timedelta(seconds=30),
        tool_budget=ToolBudget(max_tool_calls=2),
        retry_budget=RetryBudget(max_retries_per_node=1),
    )


def test_agent_run_state_is_json_serializable_and_versioned() -> None:
    state = make_state()
    payload = state.to_dict()
    encoded = json.dumps(payload)
    restored = AgentRunState.from_dict(json.loads(encoded))

    assert payload["schema_version"] == "AgentRunState@1"
    assert restored == state
    assert restored.principal_ref == {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
    }


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (GraphStatus.CREATED, GraphStatus.RUNNING),
        (GraphStatus.RUNNING, GraphStatus.WAITING_APPROVAL),
        (GraphStatus.WAITING_APPROVAL, GraphStatus.RUNNING),
        (GraphStatus.RUNNING, GraphStatus.SUCCEEDED),
        (GraphStatus.RUNNING, GraphStatus.FAILED),
        (GraphStatus.RUNNING, GraphStatus.CANCELLED),
    ],
)
def test_declared_state_transitions_are_allowed(
    source: GraphStatus, target: GraphStatus
) -> None:
    assert make_state().model_copy(update={"status": source}).can_transition_to(target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (GraphStatus.CREATED, GraphStatus.SUCCEEDED),
        (GraphStatus.SUCCEEDED, GraphStatus.RUNNING),
        (GraphStatus.FAILED, GraphStatus.RUNNING),
        (GraphStatus.CANCELLED, GraphStatus.RUNNING),
    ],
)
def test_invalid_or_terminal_state_transitions_are_rejected(
    source: GraphStatus, target: GraphStatus
) -> None:
    state = make_state().model_copy(update={"status": source})
    with pytest.raises(ValueError, match="transition"):
        state.transition_to(target)


def test_node_input_and_output_must_be_json_values() -> None:
    state = make_state()
    updated = state.record_node_result(
        NodeName.RETRIEVE,
        node_input={"query": "travel policy"},
        node_output={"document_ids": ["doc-1"], "scores": [0.95]},
    )
    json.dumps(updated.to_dict())

    with pytest.raises((TypeError, ValueError), match="JSON"):
        state.record_node_result(
            NodeName.RETRIEVE,
            node_input={"invalid": object()},
            node_output={},
        )


def test_errors_preserve_code_retryability_node_and_attempt() -> None:
    error = GraphError(
        code="RETRIEVAL_UNAVAILABLE",
        message="retriever unavailable",
        retryable=True,
        node=NodeName.RETRIEVE,
        attempt=1,
    )
    updated = make_state().record_error(error)

    assert updated.errors == (error,)
    assert updated.errors[0].code == "RETRIEVAL_UNAVAILABLE"
    assert updated.errors[0].retryable is True


def test_expired_deadline_is_detected() -> None:
    state = make_state().model_copy(
        update={"deadline_at": datetime.now(UTC) - timedelta(milliseconds=1)}
    )
    assert state.is_timed_out(now=datetime.now(UTC)) is True


def test_retry_budget_is_finite_and_scoped_per_node() -> None:
    state = make_state()
    first = state.consume_retry(NodeName.RETRIEVE)
    assert first.retry_count(NodeName.RETRIEVE) == 1
    assert first.retry_count(NodeName.GENERATE) == 0

    with pytest.raises(ValueError, match="retry"):
        first.consume_retry(NodeName.RETRIEVE)


def test_max_tool_calls_is_enforced() -> None:
    state = make_state()
    first = state.consume_tool_call("search")
    second = first.consume_tool_call("calculator")

    assert second.tool_budget.tool_calls == 2
    assert second.tool_budget.max_tool_calls == 2
    with pytest.raises(ValueError, match="tool"):
        second.consume_tool_call("third-tool")
