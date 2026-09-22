"""Assemble the single shared LangGraph (T046).

There is exactly one graph. Chat, stream and eval traverse the linear evidence
path; the file workflow is the same graph with the approval branch enabled. The
builder's job is wiring plus the run-wide guarantees that individual nodes
cannot enforce alone:

- **Interrupt boundary**: the file workflow suspends at ``approval_interrupt``
  before any side effect; the checkpointer persists the exact resume point.
- **Deterministic decision**: with deterministic dependencies the traversal and
  evidence verdict are fixed, which is what makes cross-entrypoint parity
  meaningful.

Total-deadline, tool-count and per-node retry budgets are carried in
:class:`GraphState` and enforced at node boundaries (see
:mod:`backend.app.graph.nodes`).
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from backend.app.graph.dependencies import GraphDependencies
from backend.app.graph.nodes import GraphNodes, GraphState

__all__ = ["build_graph", "build_postgres_checkpointer"]


def _route_after_plan(state: GraphState) -> str:
    """File workflow with a pending action goes through approval; else straight
    to generation."""
    if state.get("requires_approval") and state.get("pending_action"):
        return "sandbox"
    return "generate"


def build_graph(
    deps: GraphDependencies,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the shared graph. Pass a checkpointer to enable interrupt/resume."""
    nodes = GraphNodes(deps)
    graph: StateGraph = StateGraph(GraphState)

    graph.add_node("validate", nodes.validate)
    graph.add_node("memory_load", nodes.memory_load)
    graph.add_node("rewrite", nodes.rewrite)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rerank", nodes.rerank)
    graph.add_node("evidence_gate", nodes.evidence_gate)
    graph.add_node("plan_or_tool", nodes.plan_or_tool)
    graph.add_node("sandbox", nodes.sandbox)
    graph.add_node("approval_interrupt", nodes.approval_interrupt)
    graph.add_node("generate", nodes.generate)
    graph.add_node("writeback", nodes.writeback)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "validate")
    graph.add_edge("validate", "memory_load")
    graph.add_edge("memory_load", "rewrite")
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "evidence_gate")
    graph.add_edge("evidence_gate", "plan_or_tool")
    graph.add_conditional_edges(
        "plan_or_tool",
        _route_after_plan,
        {"sandbox": "sandbox", "generate": "generate"},
    )
    graph.add_edge("sandbox", "approval_interrupt")
    graph.add_edge("approval_interrupt", "generate")
    graph.add_edge("generate", "writeback")
    graph.add_edge("writeback", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


def build_postgres_checkpointer(conn_string: str) -> Any:
    """Build the durable ``langgraph-checkpoint-postgres`` async saver (T044).

    Returns an async context manager. Requires a reachable PostgreSQL and the
    one-time ``await saver.setup()`` on first use. Not exercised by the Stage 3
    contract tests (which use the in-memory saver); it is the production wiring
    the durable-run stage depends on.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    return AsyncPostgresSaver.from_conn_string(conn_string)
