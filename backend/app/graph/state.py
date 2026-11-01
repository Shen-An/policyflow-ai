"""Versioned ``AgentRunState@1`` — the single serializable graph state (T043).

Every entrypoint (chat, stream, eval, file workflow) advances one instance of
this state through the shared node sequence. The invariants encoded here are
the reason the state is trustworthy across a restart:

- **JSON round-trip is lossless.** A checkpoint is only as good as its ability
  to be reconstructed byte-for-byte, so :meth:`AgentRunState.to_dict` /
  :meth:`from_dict` are the authority and equality is defined over them.
- **State transitions are a closed graph.** ``finalize`` compares-and-sets a
  terminal state once; a terminal state can never re-open, so a duplicate
  delivery cannot resurrect a completed run.
- **Budgets are finite and per-node.** Tool calls and retries are bounded so a
  loop or a flapping dependency cannot run forever or spend without limit.

The state carries only non-secret reference identifiers (see
``contracts/internal-contracts.md`` → ``AgentRunState``); credentials, tokens
and raw provider payloads never live here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SCHEMA_VERSION",
    "AgentRunState",
    "ChatTurnState",
    "GraphError",
    "GraphStatus",
    "NodeName",
    "NodeResult",
    "RetryBudget",
    "ToolBudget",
]

SCHEMA_VERSION = "AgentRunState@1"


class GraphStatus(str, Enum):
    """Lifecycle of a single graph run."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Closed transition graph. Anything not listed here is rejected, so a terminal
# state (succeeded/failed/cancelled) has no outgoing edge and cannot re-open.
_ALLOWED_TRANSITIONS: dict[GraphStatus, frozenset[GraphStatus]] = {
    GraphStatus.CREATED: frozenset({GraphStatus.RUNNING}),
    GraphStatus.RUNNING: frozenset(
        {
            GraphStatus.WAITING_APPROVAL,
            GraphStatus.SUCCEEDED,
            GraphStatus.FAILED,
            GraphStatus.CANCELLED,
        }
    ),
    GraphStatus.WAITING_APPROVAL: frozenset({GraphStatus.RUNNING}),
    GraphStatus.SUCCEEDED: frozenset(),
    GraphStatus.FAILED: frozenset(),
    GraphStatus.CANCELLED: frozenset(),
}


class NodeName(str, Enum):
    """The typed nodes of the shared graph.

    ``sandbox`` and ``approval_interrupt`` are only reached by the file
    workflow; chat/stream/eval traverse the linear evidence path.
    """

    VALIDATE = "validate"
    MEMORY_LOAD = "memory_load"
    REWRITE = "rewrite"
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    EVIDENCE_GATE = "evidence_gate"
    PLAN_OR_TOOL = "plan_or_tool"
    SANDBOX = "sandbox"
    APPROVAL_INTERRUPT = "approval_interrupt"
    GENERATE = "generate"
    WRITEBACK = "writeback"
    FINALIZE = "finalize"


class ToolBudget(BaseModel):
    """Finite tool-call allowance for one run."""

    model_config = ConfigDict(frozen=True)

    max_tool_calls: int
    tool_calls: int = 0


class RetryBudget(BaseModel):
    """Finite per-node retry allowance."""

    model_config = ConfigDict(frozen=True)

    max_retries_per_node: int


class GraphError(BaseModel):
    """A recorded node failure that preserves enough to decide recovery."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    retryable: bool
    node: NodeName
    attempt: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "node": self.node.value,
            "attempt": self.attempt,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GraphError:
        return cls(
            code=payload["code"],
            message=payload["message"],
            retryable=payload["retryable"],
            node=NodeName(payload["node"]),
            attempt=payload["attempt"],
        )


class NodeResult(BaseModel):
    """The JSON-valued input/output recorded for one executed node."""

    model_config = ConfigDict(frozen=True)

    node: NodeName
    node_input: dict[str, Any] = Field(default_factory=dict)
    node_output: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node.value,
            "input": self.node_input,
            "output": self.node_output,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> NodeResult:
        return cls(
            node=NodeName(payload["node"]),
            node_input=payload.get("input", {}),
            node_output=payload.get("output", {}),
        )


def _require_json_value(label: str, value: Any) -> None:
    """Reject a node payload that cannot be checkpointed.

    A node result that is not JSON-serializable would silently corrupt the
    checkpoint, so we fail loudly at record time rather than at restore time.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - message asserted
        raise TypeError(f"{label} must be a JSON-serializable value: {exc}") from exc


class AgentRunState(BaseModel):
    """The one serializable state carried through the shared graph."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str
    run_id: str
    thread_id: str
    status: GraphStatus = GraphStatus.CREATED
    current_node: NodeName = NodeName.VALIDATE
    deadline_at: datetime | None = None
    tool_budget: ToolBudget
    retry_budget: RetryBudget
    node_results: tuple[NodeResult, ...] = ()
    errors: tuple[GraphError, ...] = ()
    retry_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def principal_ref(self) -> dict[str, str]:
        """Non-secret identity reference embedded in the checkpoint."""
        return {"tenant_id": self.tenant_id, "user_id": self.user_id}

    # -- transitions -----------------------------------------------------

    def can_transition_to(self, target: GraphStatus) -> bool:
        return target in _ALLOWED_TRANSITIONS.get(self.status, frozenset())

    def transition_to(self, target: GraphStatus) -> AgentRunState:
        if not self.can_transition_to(target):
            raise ValueError(
                f"illegal state transition {self.status.value} -> {target.value}"
            )
        return self.model_copy(update={"status": target})

    # -- recording -------------------------------------------------------

    def record_node_result(
        self,
        node: NodeName,
        *,
        node_input: dict[str, Any],
        node_output: dict[str, Any],
    ) -> AgentRunState:
        _require_json_value("node input", node_input)
        _require_json_value("node output", node_output)
        result = NodeResult(node=node, node_input=node_input, node_output=node_output)
        return self.model_copy(
            update={
                "node_results": (*self.node_results, result),
                "current_node": node,
            }
        )

    def record_error(self, error: GraphError) -> AgentRunState:
        return self.model_copy(update={"errors": (*self.errors, error)})

    # -- budgets ---------------------------------------------------------

    def is_timed_out(self, *, now: datetime | None = None) -> bool:
        if self.deadline_at is None:
            return False
        moment = now or datetime.now(UTC)
        return moment >= self.deadline_at

    def retry_count(self, node: NodeName) -> int:
        return self.retry_counts.get(node.value, 0)

    def consume_retry(self, node: NodeName) -> AgentRunState:
        used = self.retry_count(node)
        if used >= self.retry_budget.max_retries_per_node:
            raise ValueError(
                f"retry budget exhausted for node {node.value} "
                f"(max {self.retry_budget.max_retries_per_node})"
            )
        counts = dict(self.retry_counts)
        counts[node.value] = used + 1
        return self.model_copy(update={"retry_counts": counts})

    def consume_tool_call(self, tool_name: str) -> AgentRunState:
        if self.tool_budget.tool_calls >= self.tool_budget.max_tool_calls:
            raise ValueError(
                f"tool-call budget exhausted (max {self.tool_budget.max_tool_calls}); "
                f"refusing tool {tool_name!r}"
            )
        budget = self.tool_budget.model_copy(
            update={"tool_calls": self.tool_budget.tool_calls + 1}
        )
        return self.model_copy(update={"tool_budget": budget})

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "principal_ref": self.principal_ref,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "status": self.status.value,
            "current_node": self.current_node.value,
            "deadline_at": self.deadline_at.isoformat() if self.deadline_at else None,
            "tool_budget": {
                "max_tool_calls": self.tool_budget.max_tool_calls,
                "tool_calls": self.tool_budget.tool_calls,
            },
            "retry_budget": {
                "max_retries_per_node": self.retry_budget.max_retries_per_node,
            },
            "node_results": [result.to_dict() for result in self.node_results],
            "errors": [error.to_dict() for error in self.errors],
            "retry_counts": dict(self.retry_counts),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentRunState:
        version = payload.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported AgentRunState schema version {version!r}")
        deadline = payload.get("deadline_at")
        return cls(
            tenant_id=payload["tenant_id"],
            user_id=payload["user_id"],
            run_id=payload["run_id"],
            thread_id=payload["thread_id"],
            status=GraphStatus(payload["status"]),
            current_node=NodeName(payload["current_node"]),
            deadline_at=datetime.fromisoformat(deadline) if deadline else None,
            tool_budget=ToolBudget(**payload["tool_budget"]),
            retry_budget=RetryBudget(**payload["retry_budget"]),
            node_results=tuple(
                NodeResult.from_dict(item) for item in payload.get("node_results", [])
            ),
            errors=tuple(GraphError.from_dict(item) for item in payload.get("errors", [])),
            retry_counts=dict(payload.get("retry_counts", {})),
        )


# -- chat turn view (T053 slice 1) -----------------------------------------
#
# 与上面的 AgentRunState 刻意不同：AgentRunState 是可序列化、可落 checkpoint 的
# 持久运行态（见 _require_json_value 的强校验）；ChatTurnState 只是**一轮对话**
# 在进程内的实时视图，字段里放的是活对象（RouterResult / PlanStep / 合规结果）。
# 它的用途是让同一份节点实现（backend.app.graph.nodes 里的 chat_route_node /
# chat_tot_node / chat_execute_node）在被第二个 driver 复跑时，能把"这一轮到底
# 决定了什么"交回来做 diff —— 它不进 checkpoint，也不写数据库。


def _event_summary(payload: Any) -> Any:
    """Reduce an event payload to a small JSON-safe summary (no answer text)."""

    if isinstance(payload, dict):
        picked = {
            key: payload[key]
            for key in ("difficulty", "reasoning_mode", "plan_source", "status", "stage")
            if key in payload
        }
        return picked or {"keys": sorted(str(key) for key in payload)}
    if isinstance(payload, (list, tuple)):
        return {"items": len(payload)}
    return type(payload).__name__


class ChatTurnState(BaseModel):
    """In-process view of one chat turn's orchestration decisions.

    Not durable and not JSON-serializable by design: it carries live objects so
    two drivers can be compared field by field. See the section comment above.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: str = ""
    #: RouterAgent output for this turn (``RouterResult``), kept as a live object.
    router_result: Any | None = None
    #: Normalized plan steps chosen for this turn (``PlanStep`` instances).
    plan_steps: list[Any] = Field(default_factory=list)
    #: Tree-of-Thought candidate options when the turn went through the tot node.
    plan_options: list[Any] = Field(default_factory=list)
    #: Skill suggestions / hits collected during the turn.
    skill_hits: list[dict[str, Any]] = Field(default_factory=list)
    #: Compliance result for the produced answer (``ComplianceResult``).
    compliance: Any | None = None
    #: Structured diagnostics assembled from the turn's stage / command events.
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    #: ``"tot"`` or ``"execute"`` once the route node decided; empty before that.
    route_kind: str = ""
    #: Ordered node names visited by the driver that produced this view.
    node_trace: list[str] = Field(default_factory=list)
    #: Ordered ``(event, summary)`` pairs handed to the event stream.
    events: list[dict[str, Any]] = Field(default_factory=list)
    #: ``PipelineResult`` produced by the execute node, when one exists.
    result: Any | None = None

    def record_node(self, node: str) -> None:
        self.node_trace.append(node)

    def record_stage(self, stage: str, status: str, message: str) -> None:
        commands = self.diagnostics.setdefault("commands", [])
        commands.append({"name": stage, "status": status, "summary": message})

    def record_event(self, event: str, payload: Any) -> None:
        self.events.append({"event": event, "payload": _event_summary(payload)})

    def record_skill_hit(self, name: str, status: str, summary: str = "") -> None:
        self.skill_hits.append({"name": name, "status": status, "summary": summary})

    def event_names(self) -> list[str]:
        return [item["event"] for item in self.events]

    def summary(self) -> dict[str, Any]:
        """JSON-safe digest used for shadow-mode diffing."""

        return {
            "question": self.question,
            "route_kind": self.route_kind,
            "node_trace": list(self.node_trace),
            "event_names": self.event_names(),
            "plan_steps": len(self.plan_steps),
            "plan_options": len(self.plan_options),
            "skill_hits": len(self.skill_hits),
            "has_compliance": self.compliance is not None,
            "command_count": len(self.diagnostics.get("commands", [])),
            "has_result": self.result is not None,
        }
