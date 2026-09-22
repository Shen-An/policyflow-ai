"""Authorized checkpoint binding and checkpoint stores (T038, T041, T044).

A LangGraph thread id is an opaque handle. On its own it grants nothing — the
authority to invoke, stream or resume a thread lives in a
:class:`GraphCheckpointBinding` that ties the thread to exactly one
tenant/user/run at a known authorization version. Every graph operation
resolves the binding first and refuses anything that does not match.

Two deliberate anti-disclosure choices:

- The thread id is a random opaque token, never derived from identity, so it
  cannot be reverse-engineered into a tenant/user/run.
- An unknown thread returns the same ``GRAPH_CHECKPOINT_NOT_FOUND`` regardless
  of tenant, so a caller cannot probe which threads exist in another tenant.

Honesty boundary: the stores here are in-memory and satisfy the Stage 3
contract. The durable ``langgraph-checkpoint-postgres`` saver is Stage 4 work.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from typing import Any

from backend.app.graph.state import SCHEMA_VERSION as GRAPH_STATE_SCHEMA_VERSION

__all__ = [
    "CHECKPOINT_BINDING_SCHEMA_VERSION",
    "GraphCheckpoint",
    "GraphCheckpointBinding",
    "GraphCheckpointBindingError",
    "InMemoryGraphCheckpointBindingStore",
    "InMemoryGraphCheckpointStore",
]

CHECKPOINT_BINDING_SCHEMA_VERSION = "GraphCheckpointBinding@1"

# Graph-state schema versions this binding format is allowed to reference.
_KNOWN_GRAPH_SCHEMA_VERSIONS = frozenset({GRAPH_STATE_SCHEMA_VERSION})


class GraphCheckpointBindingError(Exception):
    """Raised when a graph operation is not authorized for a binding.

    ``public_code`` is the stable, non-disclosing error code surfaced to
    callers; the message never contains tenant/user identifiers.
    """

    def __init__(self, message: str, *, public_code: str) -> None:
        super().__init__(message)
        self.public_code = public_code


@dataclass(frozen=True)
class GraphCheckpointBinding:
    """The authority record tying an opaque thread to one run's identity."""

    tenant_id: str
    user_id: str
    run_id: str
    thread_id: str
    graph_schema_version: str
    authorization_version: int
    checkpoint_schema_version: str = CHECKPOINT_BINDING_SCHEMA_VERSION
    latest_checkpoint_ref: str | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        graph_schema_version: str,
        authorization_version: int,
    ) -> GraphCheckpointBinding:
        return cls(
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
            thread_id=f"thr_{secrets.token_urlsafe(24)}",
            graph_schema_version=graph_schema_version,
            authorization_version=authorization_version,
            checkpoint_schema_version=CHECKPOINT_BINDING_SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "graph_schema_version": self.graph_schema_version,
            "authorization_version": self.authorization_version,
            "checkpoint_schema_version": self.checkpoint_schema_version,
            "latest_checkpoint_ref": self.latest_checkpoint_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GraphCheckpointBinding:
        checkpoint_version = payload.get("checkpoint_schema_version")
        if checkpoint_version != CHECKPOINT_BINDING_SCHEMA_VERSION:
            raise GraphCheckpointBindingError(
                f"unknown checkpoint binding schema version {checkpoint_version!r}",
                public_code="GRAPH_CHECKPOINT_SCHEMA_UNSUPPORTED",
            )
        graph_version = payload.get("graph_schema_version")
        if graph_version not in _KNOWN_GRAPH_SCHEMA_VERSIONS:
            raise GraphCheckpointBindingError(
                f"unknown graph state schema version {graph_version!r}",
                public_code="GRAPH_CHECKPOINT_SCHEMA_UNSUPPORTED",
            )
        return cls(
            tenant_id=payload["tenant_id"],
            user_id=payload["user_id"],
            run_id=payload["run_id"],
            thread_id=payload["thread_id"],
            graph_schema_version=graph_version,
            authorization_version=payload["authorization_version"],
            checkpoint_schema_version=checkpoint_version,
            latest_checkpoint_ref=payload.get("latest_checkpoint_ref"),
        )


class InMemoryGraphCheckpointBindingStore:
    """A thread-id → binding lookup used to authorize graph operations."""

    def __init__(self, bindings: list[GraphCheckpointBinding] | None = None) -> None:
        self._by_thread: dict[str, GraphCheckpointBinding] = {
            binding.thread_id: binding for binding in (bindings or [])
        }

    def get(self, thread_id: str) -> GraphCheckpointBinding | None:
        return self._by_thread.get(thread_id)

    def put(self, binding: GraphCheckpointBinding) -> None:
        self._by_thread[binding.thread_id] = binding


@dataclass
class GraphCheckpoint:
    """A persisted point-in-time snapshot for one run thread."""

    tenant_id: str
    user_id: str
    run_id: str
    thread_id: str
    checkpoint_id: str
    status: str
    pending_action: dict[str, Any] | None = None
    # Idempotency ledger for approved side effects, keyed by checkpoint id.
    consumed_result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemoryGraphCheckpointStore:
    """Restart-surviving (within a process) checkpoint store for the runtime.

    Keyed by the full identity tuple so one runtime instance can be discarded
    and a fresh one can restore the exact same checkpoint — the property the
    restart tests assert.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[tuple[str, str, str, str], GraphCheckpoint] = {}

    @staticmethod
    def _key(tenant_id: str, user_id: str, run_id: str, thread_id: str) -> tuple[str, str, str, str]:
        return (tenant_id, user_id, run_id, thread_id)

    def load(
        self, *, tenant_id: str, user_id: str, run_id: str, thread_id: str
    ) -> GraphCheckpoint | None:
        return self._checkpoints.get(self._key(tenant_id, user_id, run_id, thread_id))

    def save(self, checkpoint: GraphCheckpoint) -> None:
        self._checkpoints[
            self._key(
                checkpoint.tenant_id,
                checkpoint.user_id,
                checkpoint.run_id,
                checkpoint.thread_id,
            )
        ] = replace(checkpoint)
