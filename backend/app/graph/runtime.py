"""Checkpointed graph runtime with human-in-the-loop approval (T041).

This is the small, honest core of the restart story. A file workflow runs until
it reaches an action that needs approval, then persists a
``waiting_approval`` checkpoint and stops — deliberately before any side
effect. The run can then survive the runtime being thrown away and rebuilt:

- **Restart**: a fresh :class:`GraphRuntime` over the same checkpoint store
  restores the identical checkpoint and pending action.
- **Interrupt replay is pure**: re-invoking an already-interrupted run returns
  the same checkpoint and performs no side effect.
- **Resume is fail-safe and exactly-once**: a rejected resume cancels with no
  side effect; an approved resume executes the side effect once and records the
  receipt, so a replayed approval returns the same result without re-executing.

Idempotency is enforced by the checkpoint's ``consumed_result`` ledger, not by
in-memory bookkeeping, so it holds across a restart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from backend.app.graph.checkpoints import GraphCheckpoint, InMemoryGraphCheckpointStore
from backend.app.graph.side_effects import RecordingSideEffectExecutor, SideEffectExecutor

__all__ = ["ApprovalDecision", "GraphRuntime", "RunHandle", "RunStatus"]


class RunStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RunHandle:
    status: RunStatus
    checkpoint_id: str | None = None
    pending_action: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class GraphRuntime:
    """Drives a checkpointed file workflow to and through an approval gate."""

    def __init__(
        self,
        *,
        checkpoints: InMemoryGraphCheckpointStore,
        side_effects: SideEffectExecutor | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._side_effects: SideEffectExecutor = side_effects or RecordingSideEffectExecutor()

    async def invoke_file_workflow(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        thread_id: str,
        action: dict[str, Any],
    ) -> RunHandle:
        existing = self._checkpoints.load(
            tenant_id=tenant_id, user_id=user_id, run_id=run_id, thread_id=thread_id
        )
        if existing is not None:
            # Interrupt replay is pure: return the persisted interrupt as-is,
            # never re-deciding or re-acting.
            return self._handle_from(existing)

        checkpoint = GraphCheckpoint(
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
            thread_id=thread_id,
            checkpoint_id=f"ckpt-{uuid.uuid4().hex}",
            status=RunStatus.WAITING_APPROVAL.value,
            pending_action=dict(action),
        )
        self._checkpoints.save(checkpoint)
        return self._handle_from(checkpoint)

    async def load(
        self, *, tenant_id: str, user_id: str, run_id: str, thread_id: str
    ) -> RunHandle:
        checkpoint = self._checkpoints.load(
            tenant_id=tenant_id, user_id=user_id, run_id=run_id, thread_id=thread_id
        )
        if checkpoint is None:
            raise LookupError("no checkpoint for the requested run thread")
        return self._handle_from(checkpoint)

    async def resume(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        thread_id: str,
        checkpoint_id: str,
        decision: ApprovalDecision,
    ) -> RunHandle:
        checkpoint = self._checkpoints.load(
            tenant_id=tenant_id, user_id=user_id, run_id=run_id, thread_id=thread_id
        )
        if checkpoint is None or checkpoint.checkpoint_id != checkpoint_id:
            raise LookupError("no matching checkpoint to resume")

        if decision is ApprovalDecision.REJECTED:
            checkpoint.status = RunStatus.CANCELLED.value
            self._checkpoints.save(checkpoint)
            return self._handle_from(checkpoint)

        # Approved. Exactly-once: if a receipt is already recorded, this is a
        # replay — return it without touching the side-effect executor.
        if checkpoint.consumed_result is not None:
            return self._handle_from(checkpoint)

        receipt = await self._side_effects.execute(checkpoint.pending_action or {})
        checkpoint.consumed_result = receipt
        checkpoint.status = RunStatus.SUCCEEDED.value
        self._checkpoints.save(checkpoint)
        return self._handle_from(checkpoint)

    @staticmethod
    def _handle_from(checkpoint: GraphCheckpoint) -> RunHandle:
        return RunHandle(
            status=RunStatus(checkpoint.status),
            checkpoint_id=checkpoint.checkpoint_id,
            pending_action=checkpoint.pending_action,
            result=checkpoint.consumed_result,
        )
