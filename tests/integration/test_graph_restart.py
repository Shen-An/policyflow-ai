"""Failing integration contracts for graph checkpoint restart safety (T041)."""

from __future__ import annotations

import pytest

from backend.app.graph.checkpoints import InMemoryGraphCheckpointStore
from backend.app.graph.runtime import ApprovalDecision, GraphRuntime, RunStatus
from backend.app.graph.side_effects import RecordingSideEffectExecutor


@pytest.mark.asyncio
async def test_waiting_approval_checkpoint_survives_runtime_restart() -> None:
    checkpoints = InMemoryGraphCheckpointStore()
    side_effects = RecordingSideEffectExecutor()
    first_runtime = GraphRuntime(checkpoints=checkpoints, side_effects=side_effects)

    interrupted = await first_runtime.invoke_file_workflow(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        action={"kind": "submit_expense", "amount": 125},
    )

    assert interrupted.status is RunStatus.WAITING_APPROVAL
    assert interrupted.checkpoint_id
    assert side_effects.calls == []

    restarted_runtime = GraphRuntime(checkpoints=checkpoints, side_effects=side_effects)
    restored = await restarted_runtime.load(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
    )

    assert restored.status is RunStatus.WAITING_APPROVAL
    assert restored.checkpoint_id == interrupted.checkpoint_id
    assert restored.pending_action == interrupted.pending_action
    assert side_effects.calls == []


@pytest.mark.asyncio
async def test_interrupt_replay_is_pure_and_idempotent() -> None:
    checkpoints = InMemoryGraphCheckpointStore()
    side_effects = RecordingSideEffectExecutor()
    runtime = GraphRuntime(checkpoints=checkpoints, side_effects=side_effects)
    request = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "thread_id": "opaque-thread-a",
        "action": {"kind": "submit_expense", "amount": 125},
    }

    first = await runtime.invoke_file_workflow(**request)
    replayed = await runtime.invoke_file_workflow(**request)

    assert first.status is RunStatus.WAITING_APPROVAL
    assert replayed.status is RunStatus.WAITING_APPROVAL
    assert replayed.checkpoint_id == first.checkpoint_id
    assert replayed.pending_action == first.pending_action
    assert side_effects.calls == []


@pytest.mark.asyncio
async def test_unapproved_or_rejected_resume_has_no_side_effect() -> None:
    checkpoints = InMemoryGraphCheckpointStore()
    side_effects = RecordingSideEffectExecutor()
    runtime = GraphRuntime(checkpoints=checkpoints, side_effects=side_effects)
    waiting = await runtime.invoke_file_workflow(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        action={"kind": "submit_expense", "amount": 125},
    )

    rejected = await runtime.resume(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        checkpoint_id=waiting.checkpoint_id,
        decision=ApprovalDecision.REJECTED,
    )

    assert rejected.status is RunStatus.CANCELLED
    assert side_effects.calls == []


@pytest.mark.asyncio
async def test_approved_resume_executes_side_effect_exactly_once_after_restart() -> None:
    checkpoints = InMemoryGraphCheckpointStore()
    side_effects = RecordingSideEffectExecutor()
    first_runtime = GraphRuntime(checkpoints=checkpoints, side_effects=side_effects)
    waiting = await first_runtime.invoke_file_workflow(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        action={"kind": "submit_expense", "amount": 125},
    )

    restarted_runtime = GraphRuntime(checkpoints=checkpoints, side_effects=side_effects)
    first_resume = await restarted_runtime.resume(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        checkpoint_id=waiting.checkpoint_id,
        decision=ApprovalDecision.APPROVED,
    )
    replayed_resume = await restarted_runtime.resume(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        thread_id="opaque-thread-a",
        checkpoint_id=waiting.checkpoint_id,
        decision=ApprovalDecision.APPROVED,
    )

    assert first_resume.status is RunStatus.SUCCEEDED
    assert replayed_resume.status is RunStatus.SUCCEEDED
    assert first_resume.result == replayed_resume.result
    assert len(side_effects.calls) == 1
