"""Unified graph service surface (T038, T039, T049).

One service authorizes and drives every graph operation. Two responsibilities
live here:

- **Authorized checkpoint operations** (``invoke``/``stream``/``resume``): each
  resolves the :class:`GraphCheckpointBinding` for an opaque thread and refuses
  any caller whose identity or authorization version does not match. This is
  the enforcement point for "a thread grants nothing on its own".
- **Deterministic entrypoint execution** (``execute``): runs the shared node
  sequence with side effects disabled so chat/stream/eval/file all produce the
  same node order and the same decision fingerprint.

The ``GraphRunner`` protocol and its request/result types are the contract the
temporary legacy adapter (T051–T053) speaks. Wiring ``GraphService`` itself to
implement ``GraphRunner`` against the real provider and retrieval is the
remaining Stage 3 service work; it is intentionally not faked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from backend.app.auth.principal import RequestPrincipal
from backend.app.graph.checkpoints import (
    GraphCheckpointBindingError,
    InMemoryGraphCheckpointBindingStore,
)
from backend.app.graph.entrypoints import (
    ENTRYPOINT_REQUIRED_SCOPE,
    GraphEntrypoint,
    GraphRequest,
)

__all__ = [
    "SHARED_NODE_SEQUENCE",
    "ExecutionResult",
    "GraphRunRequest",
    "GraphRunResult",
    "GraphRunner",
    "GraphService",
]

# The linear evidence path every non-file entrypoint traverses, and the base
# path the file workflow extends with sandbox/approval nodes.
SHARED_NODE_SEQUENCE: tuple[str, ...] = (
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

_SCOPE_WILDCARD = "graph:*"


@dataclass(frozen=True)
class ExecutionResult:
    node_sequence: tuple[str, ...]
    decision_fingerprint: str


def _result_from_state(run_id: str, final_state: dict[str, Any]) -> GraphRunResult:
    return GraphRunResult(
        run_id=run_id,
        answer=str(final_state.get("answer", "")),
        evidence=tuple(final_state.get("evidence", ())),
        evidence_gate=str(final_state.get("gate_decision", "insufficient_evidence")),
    )


@dataclass(frozen=True)
class GraphRunRequest:
    """One invocation of the shared graph via the runner facade.

    The ``allow_*`` capability flags let a caller (notably the shadow-mode
    legacy adapter) run the decision path with every side effect disabled.
    """

    entrypoint: str
    tenant_id: str
    user_id: str
    run_id: str
    input_payload: dict[str, Any] = field(default_factory=dict)
    conversation_id: str | None = None
    deterministic: bool = False
    allow_tools: bool = True
    allow_writeback: bool = True
    allow_files: bool = True
    allow_connectors: bool = True


@dataclass(frozen=True)
class GraphRunResult:
    run_id: str
    answer: str
    evidence: tuple[str, ...] = ()
    evidence_gate: str = "supported"


@runtime_checkable
class GraphRunner(Protocol):
    """What the legacy adapter depends on; implemented by the real service."""

    async def run(self, request: GraphRunRequest) -> GraphRunResult: ...


class GraphService:
    """Authorizes and executes shared-graph operations."""

    def __init__(
        self,
        *,
        checkpoint_bindings: InMemoryGraphCheckpointBindingStore | None = None,
        deterministic: bool = False,
        dependencies: Any = None,
        uow_factory: Any = None,
    ) -> None:
        self._bindings = checkpoint_bindings or InMemoryGraphCheckpointBindingStore()
        self._deterministic = deterministic
        # Injected GraphDependencies for the production run path. When absent
        # (or in deterministic mode) the reproducible deps are used.
        self._dependencies = dependencies
        # Optional async Unit-of-Work factory. When present, each run persists
        # an AgentRun + ordered RunEvents so the run is durable and auditable by
        # ``run_id``; when absent, run() is a pure in-memory execution.
        self._uow_factory = uow_factory

    @classmethod
    def deterministic(cls) -> GraphService:
        """A service that runs entrypoints deterministically (for eval/parity)."""
        return cls(deterministic=True)

    # -- unified runner facade (GraphRunner) -----------------------------

    async def run(self, request: GraphRunRequest) -> GraphRunResult:
        """Execute the shared graph for one legacy/compat invocation.

        Implements the :class:`GraphRunner` protocol the legacy adapter speaks,
        so chat and eval reach the same graph and the same evidence gate.
        """
        import uuid

        run_id = request.run_id or f"run-{uuid.uuid4().hex}"
        thread_id = f"thr_{uuid.uuid4().hex}"

        if self._uow_factory is None:
            final_state = await self._run_graph(request, run_id)
            return _result_from_state(run_id, final_state)

        # Durable path: persist the run and ordered events by run_id so it is
        # recoverable and auditable. Compare-and-set on every status write.
        async with self._uow_factory() as uow:
            await uow.set_tenant_context(request.tenant_id)
            row = await uow.runs.create(
                request.tenant_id,
                user_id=request.user_id,
                kind=request.entrypoint,
                thread_id=thread_id,
                run_id=run_id,
                conversation_id=request.conversation_id,
                input_snapshot={"entrypoint": request.entrypoint},
                status="queued",
            )
            # Each successful compare-and-set increments version by one. We track
            # it locally rather than re-reading, because the update runs with
            # ``synchronize_session=False`` and the returned row is stale.
            version = row.version
            await uow.runs.set_status(request.tenant_id, run_id, "running", version)
            version += 1
            await uow.run_events.append(
                request.tenant_id, run_id, "run.created", stage="validate", public_status="running"
            )

            final_state = await self._run_graph(request, run_id)
            result = _result_from_state(run_id, final_state)

            await uow.runs.set_evidence_gate(
                request.tenant_id, run_id, result.evidence_gate, version
            )
            version += 1
            await uow.run_events.append(
                request.tenant_id,
                run_id,
                "evidence.gate",
                stage="evidence_gate",
                public_status=result.evidence_gate,
                payload={"accepted_evidence": list(result.evidence)},
            )
            await uow.runs.set_result_snapshot(
                request.tenant_id,
                run_id,
                {"answer": result.answer, "evidence_gate": result.evidence_gate},
                version,
            )
            version += 1
            terminal = "succeeded" if final_state.get("status") == "succeeded" else "failed"
            await uow.runs.set_status(request.tenant_id, run_id, terminal, version)
            await uow.run_events.append(
                request.tenant_id, run_id, "run.finalized", stage="finalize", public_status=terminal
            )
            await uow.commit()
        return result

    async def _run_graph(self, request: GraphRunRequest, run_id: str) -> dict[str, Any]:
        from backend.app.graph.builder import build_graph
        from backend.app.graph.dependencies import DeterministicGraphDependencies

        deps = self._dependencies
        if deps is None or request.deterministic or self._deterministic:
            deps = DeterministicGraphDependencies(tenant_id=request.tenant_id)
        graph = build_graph(deps)
        return await graph.ainvoke(
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "run_id": run_id,
                "conversation_id": request.conversation_id,
                "entrypoint": request.entrypoint,
                "query": str(
                    request.input_payload.get("query")
                    or request.input_payload.get("message")
                    or request.input_payload.get("question")
                    or ""
                ),
                "knowledge_version": str(request.input_payload.get("knowledge_version", "unversioned")),
                "deterministic": bool(request.deterministic),
                "history": list(request.input_payload.get("history", [])),
                "max_tool_calls": 0,
                "tool_calls": 0,
                "requires_approval": False,
                "visited": [],
            }
        )

    async def cancel(self, *, tenant_id: str, run_id: str, expected_version: int) -> str:
        """Cancel a durable run under compare-and-set; append an audit event.

        Requires a Unit-of-Work factory. The repository's state machine refuses
        to cancel an already-terminal run, so a duplicate cancel is a no-op
        transition rather than a second side effect.
        """
        if self._uow_factory is None:
            raise RuntimeError("cancel requires a persistence-backed GraphService")
        async with self._uow_factory() as uow:
            await uow.set_tenant_context(tenant_id)
            # A successful compare-and-set means the DB now holds "cancelled";
            # the returned row is stale under synchronize_session=False.
            await uow.runs.set_status(tenant_id, run_id, "cancelled", expected_version)
            await uow.run_events.append(
                tenant_id, run_id, "run.cancelled", stage="finalize", public_status="cancelled"
            )
            await uow.commit()
            return "cancelled"

    # -- authorized checkpoint operations --------------------------------

    def _authorize_binding(
        self, *, principal: RequestPrincipal, run_id: str, thread_id: str
    ) -> None:
        binding = self._bindings.get(thread_id)
        if binding is None:
            # Same code for every tenant: an unknown thread must not reveal
            # whether it exists elsewhere.
            raise GraphCheckpointBindingError(
                "no checkpoint binding for the requested thread",
                public_code="GRAPH_CHECKPOINT_NOT_FOUND",
            )
        identity_matches = (
            principal.tenant_id == binding.tenant_id
            and principal.user_id == binding.user_id
            and principal.run_id == binding.run_id
            and run_id == binding.run_id
        )
        if not identity_matches:
            raise GraphCheckpointBindingError(
                "caller is not authorized for this checkpoint binding",
                public_code="GRAPH_BINDING_FORBIDDEN",
            )
        if principal.authorization_version < binding.authorization_version:
            raise GraphCheckpointBindingError(
                "authorization version is stale for this binding",
                public_code="AUTHORIZATION_STALE",
            )

    async def invoke(
        self,
        *,
        principal: RequestPrincipal,
        run_id: str,
        thread_id: str,
        input_payload: dict[str, Any],
    ) -> None:
        self._authorize_binding(principal=principal, run_id=run_id, thread_id=thread_id)

    async def stream(
        self,
        *,
        principal: RequestPrincipal,
        run_id: str,
        thread_id: str,
        input_payload: dict[str, Any],
    ) -> None:
        self._authorize_binding(principal=principal, run_id=run_id, thread_id=thread_id)

    async def resume(
        self,
        *,
        principal: RequestPrincipal,
        run_id: str,
        thread_id: str,
        input_payload: dict[str, Any],
    ) -> None:
        self._authorize_binding(principal=principal, run_id=run_id, thread_id=thread_id)

    # -- deterministic entrypoint execution ------------------------------

    async def execute(self, request: GraphRequest) -> ExecutionResult:
        self._require_scope(request)
        knowledge_version = str(request.input_payload.get("knowledge_version", "unversioned"))
        seed = request.decision_seed or "no-seed"

        # Run the one shared graph deterministically and observe its actual
        # traversal, so parity is a property of the graph, not a hardcoded list.
        from backend.app.graph.builder import build_graph
        from backend.app.graph.dependencies import DeterministicGraphDependencies

        deps = DeterministicGraphDependencies(tenant_id=request.principal.tenant_id)
        graph = build_graph(deps)
        final_state = await graph.ainvoke(
            {
                "tenant_id": request.principal.tenant_id,
                "user_id": request.principal.user_id,
                "run_id": request.run_id,
                "entrypoint": request.entrypoint.value,
                "query": str(request.input_payload.get("query", "")),
                "knowledge_version": knowledge_version,
                "decision_seed": seed,
                "deterministic": True,
                "history": [],
                "max_tool_calls": 0,
                "tool_calls": 0,
                "requires_approval": False,
                "visited": [],
            }
        )
        node_sequence = tuple(final_state.get("visited", ()))
        gate = str(final_state.get("gate_decision", "insufficient_evidence"))
        fingerprint = f"{seed}:{knowledge_version}:{gate}"
        return ExecutionResult(
            node_sequence=node_sequence,
            decision_fingerprint=fingerprint,
        )

    @staticmethod
    def _require_scope(request: GraphRequest) -> None:
        required = ENTRYPOINT_REQUIRED_SCOPE[request.entrypoint]
        scopes = request.principal.effective_scopes()
        if required in scopes or _SCOPE_WILDCARD in scopes:
            return
        raise PermissionError(f"missing required scope {required}")
