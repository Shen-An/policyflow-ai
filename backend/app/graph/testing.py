"""In-repo graph test doubles (T042).

``RecordingGraphService`` implements the :class:`GraphRunner` protocol and
records everything it is asked to do: the requests it received and any tool,
writeback, file or connector activity. It only performs a capability when the
request allows it, so a shadow-mode caller (all capabilities disabled) leaves
every activity list empty — which is exactly what the adapter contract asserts.

This lives in the production package (not under ``tests/``) because the legacy
adapter contract and future parity harnesses both depend on a stable double.
"""

from __future__ import annotations

import uuid

from backend.app.graph.service import GraphRunRequest, GraphRunResult

__all__ = ["RecordingGraphService"]


class RecordingGraphService:
    """A ``GraphRunner`` that records calls and honors capability flags."""

    def __init__(self, *, answer: str, evidence_gate: str = "supported") -> None:
        self._answer = answer
        self._evidence_gate = evidence_gate
        self._evidence: tuple[str, ...] = ("evidence-1",) if evidence_gate == "supported" else ()
        self.calls: list[GraphRunRequest] = []
        self.tool_calls: list[dict[str, str]] = []
        self.writebacks: list[dict[str, str]] = []
        self.file_operations: list[dict[str, str]] = []
        self.connector_calls: list[dict[str, str]] = []
        self.last_result: GraphRunResult | None = None

    async def run(self, request: GraphRunRequest) -> GraphRunResult:
        self.calls.append(request)
        run_id = f"run-{uuid.uuid4().hex}"

        # Perform each capability only when the request permits it. Shadow mode
        # disables all four, so these lists must stay empty.
        if request.allow_tools:
            self.tool_calls.append({"run_id": run_id, "tool": "retrieval"})
        if request.allow_writeback:
            self.writebacks.append({"run_id": run_id, "kind": "memory"})
        if request.allow_files:
            self.file_operations.append({"run_id": run_id, "kind": "workspace"})
        if request.allow_connectors:
            self.connector_calls.append({"run_id": run_id, "kind": "submission"})

        result = GraphRunResult(
            run_id=run_id,
            answer=self._answer,
            evidence=self._evidence,
            evidence_gate=self._evidence_gate,
        )
        self.last_result = result
        return result
