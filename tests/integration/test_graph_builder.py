"""Verifies the real LangGraph assembly (T045, T046).

Distinct from the entrypoint-parity contract (which asserts the observable node
order): this exercises the compiled graph's behavior — the file-workflow
interrupt boundary, deadline enforcement and the finite tool budget.
"""

from __future__ import annotations

import time

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from backend.app.graph.builder import build_graph
from backend.app.graph.dependencies import DeterministicGraphDependencies
from backend.app.graph.nodes import GraphNodeError

LINEAR_NODES = [
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
]


def _base_state(**overrides) -> dict:
    state = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "entrypoint": "chat",
        "query": "Can I claim approved travel expenses?",
        "knowledge_version": "v1",
        "deterministic": True,
        "history": [],
        "max_tool_calls": 1,
        "tool_calls": 0,
        "requires_approval": False,
        "visited": [],
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_linear_chat_run_visits_shared_sequence_and_answers() -> None:
    graph = build_graph(DeterministicGraphDependencies())
    final = await graph.ainvoke(_base_state())

    assert final["visited"] == LINEAR_NODES
    assert final["gate_decision"] == "supported"
    assert final["status"] == "succeeded"
    assert final["answer"].startswith("Based on policy:")


@pytest.mark.asyncio
async def test_file_workflow_interrupts_before_generation_then_resumes() -> None:
    graph = build_graph(DeterministicGraphDependencies(), checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-file-a"}}

    interrupted = await graph.ainvoke(
        _base_state(
            entrypoint="file_workflow",
            requires_approval=True,
            pending_action={"kind": "submit_expense", "amount": 125},
        ),
        config,
    )

    # Suspended at the approval boundary: sandbox staged, but the interrupting
    # node's update (and generation) has not yet committed — no side effect.
    assert "__interrupt__" in interrupted
    assert "sandbox" in interrupted["visited"]
    assert "generate" not in interrupted["visited"]

    resumed = await graph.ainvoke(Command(resume="approved"), config)
    assert resumed["approval_decision"] == "approved"
    assert "approval_interrupt" in resumed["visited"]
    assert "generate" in resumed["visited"]
    assert resumed["status"] == "succeeded"


@pytest.mark.asyncio
async def test_expired_deadline_fails_closed() -> None:
    graph = build_graph(DeterministicGraphDependencies())
    with pytest.raises(GraphNodeError, match="deadline"):
        await graph.ainvoke(_base_state(deadline_epoch=time.monotonic() - 1.0))


@pytest.mark.asyncio
async def test_tool_budget_is_finite() -> None:
    graph = build_graph(DeterministicGraphDependencies(), checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-budget-a"}}
    with pytest.raises(GraphNodeError, match="budget"):
        await graph.ainvoke(
            _base_state(
                entrypoint="file_workflow",
                requires_approval=True,
                pending_action={"kind": "submit_expense", "amount": 1},
                max_tool_calls=0,
            ),
            config,
        )
