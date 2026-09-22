"""Typed node contracts for the shared LangGraph (T045).

Each node is a small async function over :class:`GraphState`. Nodes are the only
place work happens; the builder (:mod:`backend.app.graph.builder`) just wires
them and enforces the run-wide budgets. The contract for every node:

- It reads only declared inputs and returns a partial state update — no hidden
  globals, so a checkpoint restore fully determines behavior.
- It appends its name to ``visited`` (via an add-reducer) so the traversal is
  observable and parity-checkable.
- Retry/deadline/tool budgets are enforced structurally, not per-node ad hoc:
  the deadline is checked at every node boundary; the tool budget is checked in
  ``plan_or_tool``; ``finalize`` is a compare-and-set that runs once.

The linear evidence path (validate → … → finalize) is shared by chat, stream
and eval. The file workflow additionally routes through ``sandbox`` and
``approval_interrupt`` before ``generate``.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.types import interrupt

from backend.app.graph.dependencies import GraphDependencies
from backend.app.graph.evidence_gate import (
    EvidenceCandidate,
    EvidenceGateDecision,
    EvidenceGateInput,
    evaluate_evidence_gate,
)

__all__ = ["GraphNodeError", "GraphNodes", "GraphState"]


class GraphNodeError(Exception):
    """A typed node failure carrying a stable code and retryability."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class GraphState(TypedDict, total=False):
    # request / identity
    tenant_id: str
    user_id: str
    run_id: str
    conversation_id: str | None
    entrypoint: str
    query: str
    knowledge_version: str
    decision_seed: str
    deterministic: bool
    history: list[dict[str, Any]]
    # run-wide budgets
    deadline_epoch: float | None
    max_tool_calls: int
    tool_calls: int
    # accumulated results
    visited: Annotated[list[str], operator.add]
    rewritten_query: str
    memory_snapshot: dict[str, Any]
    candidates: list[dict[str, Any]]
    accepted_evidence_ids: list[str]
    gate_decision: str
    gate_reason: str
    # file workflow
    requires_approval: bool
    pending_action: dict[str, Any] | None
    approval_decision: str | None
    # output
    answer: str
    evidence: list[str]
    status: str
    finalized: bool


def _check_deadline(state: GraphState) -> None:
    deadline = state.get("deadline_epoch")
    if deadline is not None and time.monotonic() >= deadline:
        raise GraphNodeError(
            "DEADLINE_EXCEEDED", "run deadline exceeded", retryable=False
        )


def _candidates_from_state(state: GraphState) -> list[EvidenceCandidate]:
    return [EvidenceCandidate(**item) for item in state.get("candidates", [])]


class GraphNodes:
    """The graph's node implementations bound to one set of dependencies."""

    def __init__(self, deps: GraphDependencies) -> None:
        self._deps = deps

    async def validate(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        if not state.get("tenant_id") or not state.get("user_id"):
            raise GraphNodeError(
                "VALIDATION_FAILED", "principal identity is required", retryable=False
            )
        if not str(state.get("query", "")).strip():
            raise GraphNodeError(
                "VALIDATION_FAILED", "query is required", retryable=False
            )
        return {"visited": ["validate"], "status": "running"}

    async def memory_load(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        snapshot = await self._deps.load_memory(
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            conversation_id=state.get("conversation_id"),
        )
        return {"visited": ["memory_load"], "memory_snapshot": snapshot}

    async def rewrite(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        rewritten = await self._deps.rewrite_query(
            query=state["query"], history=state.get("history", [])
        )
        return {"visited": ["rewrite"], "rewritten_query": rewritten}

    async def retrieve(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        query = state.get("rewritten_query") or state["query"]
        candidates = await self._deps.retrieve(
            tenant_id=state["tenant_id"],
            query=query,
            knowledge_version=str(state.get("knowledge_version", "unversioned")),
        )
        return {
            "visited": ["retrieve"],
            "candidates": [vars(candidate) for candidate in candidates],
        }

    async def rerank(self, state: GraphState) -> dict[str, Any]:
        # Honest local lexical ordering by relevance — not a cross-encoder.
        _check_deadline(state)
        ranked = sorted(
            state.get("candidates", []),
            key=lambda item: item.get("relevance_score", 0.0),
            reverse=True,
        )
        return {"visited": ["rerank"], "candidates": ranked}

    async def evidence_gate(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        result = evaluate_evidence_gate(
            EvidenceGateInput(
                tenant_id=state["tenant_id"],
                query=state.get("rewritten_query") or state["query"],
                candidates=_candidates_from_state(state),
                retrieval_available=True,
                entrypoint=state.get("entrypoint", "chat"),
            )
        )
        return {
            "visited": ["evidence_gate"],
            "gate_decision": result.decision.value,
            "gate_reason": result.reason,
            "accepted_evidence_ids": list(result.accepted_evidence_ids),
        }

    async def plan_or_tool(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        # A tool request must fit the finite budget. The file workflow marks a
        # pending action requiring approval; chat/eval do not.
        if state.get("requires_approval") and state.get("pending_action"):
            max_calls = int(state.get("max_tool_calls", 0))
            used = int(state.get("tool_calls", 0))
            if used >= max_calls:
                raise GraphNodeError(
                    "TOOL_BUDGET_EXCEEDED",
                    f"tool-call budget exhausted (max {max_calls})",
                    retryable=False,
                )
            return {"visited": ["plan_or_tool"], "tool_calls": used + 1}
        return {"visited": ["plan_or_tool"]}

    async def sandbox(self, state: GraphState) -> dict[str, Any]:
        # File workflow only. Stages the action; never executes a side effect.
        _check_deadline(state)
        return {"visited": ["sandbox"]}

    async def approval_interrupt(self, state: GraphState) -> dict[str, Any]:
        # Human-in-the-loop boundary. interrupt() persists the checkpoint and
        # suspends the run *before* any side effect. Resuming supplies the
        # decision. Nodes before this point must be pure/idempotent.
        decision = interrupt(
            {
                "kind": "approval_required",
                "pending_action": state.get("pending_action"),
                "accepted_evidence_ids": state.get("accepted_evidence_ids", []),
            }
        )
        return {"visited": ["approval_interrupt"], "approval_decision": str(decision)}

    async def generate(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        accepted_ids = set(state.get("accepted_evidence_ids", []))
        accepted = [
            candidate
            for candidate in _candidates_from_state(state)
            if candidate.evidence_id in accepted_ids
        ]
        if state.get("gate_decision") != EvidenceGateDecision.SUPPORTED.value:
            return {
                "visited": ["generate"],
                "answer": "I don't have reliable policy evidence to answer that.",
                "evidence": [],
            }
        answer = await self._deps.generate(
            query=state.get("rewritten_query") or state["query"],
            accepted_evidence=accepted,
        )
        return {
            "visited": ["generate"],
            "answer": answer,
            "evidence": sorted(accepted_ids),
        }

    async def writeback(self, state: GraphState) -> dict[str, Any]:
        # Idempotent, non-authoritative. Memory writeback is intentionally a
        # no-op in the deterministic path; production deps persist here.
        _check_deadline(state)
        return {"visited": ["writeback"]}

    async def finalize(self, state: GraphState) -> dict[str, Any]:
        # Compare-and-set: only the first finalize sets the terminal state.
        if state.get("finalized"):
            return {}
        succeeded = state.get("gate_decision") is not None
        return {
            "visited": ["finalize"],
            "finalized": True,
            "status": "succeeded" if succeeded else "failed",
        }
