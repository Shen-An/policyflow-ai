"""T105 [US1] the graph approval boundary wired to the real submission.

Runs against real PostgreSQL (the submission service is PG-backed); skips cleanly
when none is reachable. It drives the actual :class:`GraphRuntime` -- the same
interrupt/resume machinery the chat/eval graph uses -- with the
:class:`FileWorkflowSideEffectExecutor` in place of the recording double, and
asserts the end-to-end invariants hold against the real approval/submission state:

* a run suspended at approval and **rejected** performs no submission -- zero jobs,
  zero accepted provider actions;
* an **approved** resume performs exactly one submission (``status=mock``);
* a **replayed** approved resume returns the recorded receipt and does not submit
  again;
* a resume whose authority was revoked after approval records a blocked outcome
  and still performs no submission.

The graph decides *when* (only post-approval, once); the submission service
decides *safe* (fresh re-auth) and *at most once* (unique key). This test proves
the two are wired together, not just correct in isolation.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.approvals.service import ChangeItemSpec
from backend.app.db.models import SubmissionJob
from backend.app.graph.checkpoints import InMemoryGraphCheckpointStore
from backend.app.graph.file_workflow import FileWorkflowSideEffectExecutor
from backend.app.graph.runtime import ApprovalDecision, GraphRuntime, RunStatus
from tests.stage6_env import APPROVER, EMPLOYEE, TENANT_A, stage6_environment

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def env(pg_url: str):
    async with stage6_environment(pg_url=pg_url, scratch_name="pf_s6_graph") as environment:
        yield environment


async def _approved_action(env) -> dict:
    """Create an approved approval and return the graph pending-action for it."""
    digest_input = {
        "action": "reimbursement_submit",
        "destination": "expense-system",
        "source_versions": [env.material_version_id],
        "output_versions": ["draft-1"],
        "file_hashes": {"form.txt": "a" * 64},
        "diff": "x",
        "evidence_set": ["ev-1"],
        "permission_snapshot": {"tenant_id": TENANT_A, "user_id": APPROVER},
        "side_effects": ["external_submission"],
    }
    change_set_id = await env.approvals.create_change_set(
        tenant_id=TENANT_A,
        workspace_id=env.workspace_id,
        run_id="run-1",
        items=[
            ChangeItemSpec(
                operation="update",
                path="forms/reimbursement.txt",
                source_version_id=env.material_version_id,
            )
        ],
    )
    from backend.app.approvals.digest import ActionDigestInput

    approval_id, digest = await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination="expense-system",
        requested_by=EMPLOYEE,
        digest_input=ActionDigestInput(**digest_input),
    )
    from backend.app.db.models import ApprovalRequest

    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        version = approval.version
    await env.approvals.decide(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        decision="approve",
        decided_by=APPROVER,
        expected_digest=digest,
        expected_version=version,
    )
    return {
        "tenant_id": TENANT_A,
        "approval_id": approval_id,
        "destination": "expense-system",
        "digest_input": digest_input,
    }


def _runtime(env) -> GraphRuntime:
    return GraphRuntime(
        checkpoints=InMemoryGraphCheckpointStore(),
        side_effects=FileWorkflowSideEffectExecutor(submissions=env.submissions),
    )


async def _jobs(env) -> list[SubmissionJob]:
    async with env.factory() as session:
        return list((await session.execute(select(SubmissionJob))).scalars().all())


async def test_reject_performs_no_submission(env) -> None:
    action = await _approved_action(env)
    runtime = _runtime(env)
    handle = await runtime.invoke_file_workflow(
        tenant_id=TENANT_A, user_id=EMPLOYEE, run_id="run-1", thread_id="th-1", action=action
    )
    assert handle.status is RunStatus.WAITING_APPROVAL

    resumed = await runtime.resume(
        tenant_id=TENANT_A,
        user_id=EMPLOYEE,
        run_id="run-1",
        thread_id="th-1",
        checkpoint_id=handle.checkpoint_id,
        decision=ApprovalDecision.REJECTED,
    )
    assert resumed.status is RunStatus.CANCELLED
    assert await _jobs(env) == [], "a rejected run must perform no submission"
    assert env.connector._accepted == {}  # noqa: SLF001


async def test_approve_submits_exactly_once(env) -> None:
    action = await _approved_action(env)
    runtime = _runtime(env)
    handle = await runtime.invoke_file_workflow(
        tenant_id=TENANT_A, user_id=EMPLOYEE, run_id="run-1", thread_id="th-1", action=action
    )
    resumed = await runtime.resume(
        tenant_id=TENANT_A,
        user_id=EMPLOYEE,
        run_id="run-1",
        thread_id="th-1",
        checkpoint_id=handle.checkpoint_id,
        decision=ApprovalDecision.APPROVED,
    )
    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.result["submitted"] is True
    assert resumed.result["status"] == "mock"
    jobs = await _jobs(env)
    assert len(jobs) == 1 and jobs[0].state == "succeeded"


async def test_replayed_approve_does_not_submit_again(env) -> None:
    action = await _approved_action(env)
    runtime = _runtime(env)
    handle = await runtime.invoke_file_workflow(
        tenant_id=TENANT_A, user_id=EMPLOYEE, run_id="run-1", thread_id="th-1", action=action
    )
    first = await runtime.resume(
        tenant_id=TENANT_A,
        user_id=EMPLOYEE,
        run_id="run-1",
        thread_id="th-1",
        checkpoint_id=handle.checkpoint_id,
        decision=ApprovalDecision.APPROVED,
    )
    second = await runtime.resume(
        tenant_id=TENANT_A,
        user_id=EMPLOYEE,
        run_id="run-1",
        thread_id="th-1",
        checkpoint_id=handle.checkpoint_id,
        decision=ApprovalDecision.APPROVED,
    )
    assert first.result == second.result, "a replay returns the recorded receipt"
    assert len(await _jobs(env)) == 1, "a replayed approve must not submit again"
    assert len(env.connector._accepted) == 1  # noqa: SLF001


async def test_revoked_authority_at_resume_blocks_without_submitting(env) -> None:
    action = await _approved_action(env)
    runtime = _runtime(env)
    handle = await runtime.invoke_file_workflow(
        tenant_id=TENANT_A, user_id=EMPLOYEE, run_id="run-1", thread_id="th-1", action=action
    )
    # Authority is revoked after approval but before the approved resume executes.
    env.allowed.discard((TENANT_A, APPROVER, "submit"))
    resumed = await runtime.resume(
        tenant_id=TENANT_A,
        user_id=EMPLOYEE,
        run_id="run-1",
        thread_id="th-1",
        checkpoint_id=handle.checkpoint_id,
        decision=ApprovalDecision.APPROVED,
    )
    assert resumed.result["submitted"] is False
    assert resumed.result["error_code"] == "AUTH_FORBIDDEN"
    assert await _jobs(env) == [], "a blocked resume must perform no submission"
    assert env.connector._accepted == {}  # noqa: SLF001
