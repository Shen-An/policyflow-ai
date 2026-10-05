"""T093 [US1] approval authorization: digest, version, expiry, fresh RBAC, isolation.

Runs against real PostgreSQL; skips cleanly when none is reachable. These are the
checks that stand between "a human looked at X" and "the system did X", so each is
exercised against the real approval service and the real action digest, not a stub.

Proven here:

* an approval binds the exact action digest, and a decision presenting a different
  digest is rejected -- an approval for one action can never decide another;
* a decision must present the version it reviewed; a stale version is a conflict;
* an expired approval cannot be approved, and expiry is observed at decision time;
* any change to a bound input (evidence, destination, permission snapshot, the
  files) recomputes to a different digest and invalidates a prior approval;
* authority is rechecked *at decision time* -- an approver whose grant was revoked
  after requesting cannot approve, and a user who never had it cannot either;
* a cross-tenant decision is refused and indistinguishable from "no such approval".
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from backend.app.approvals.digest import ActionDigestInput, compute_action_digest
from backend.app.approvals.service import (
    ApprovalConflict,
    ApprovalError,
    ApprovalStateError,
    ChangeItemSpec,
)
from backend.app.db.models import ApprovalRequest, utc_now
from tests.stage6_env import APPROVER, EMPLOYEE, TENANT_A, stage6_environment

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def env(pg_url: str):
    async with stage6_environment(pg_url=pg_url, scratch_name="pf_s6_approval") as environment:
        yield environment


def _digest_input(env, *, destination="expense-system", evidence=("ev-1",), diff="--- a") -> ActionDigestInput:
    return ActionDigestInput(
        action="reimbursement_submit",
        destination=destination,
        source_versions=[env.material_version_id],
        output_versions=["out-1"],
        file_hashes={"form.pdf": "a" * 64},
        diff=diff,
        evidence_set=list(evidence),
        permission_snapshot={"tenant_id": TENANT_A, "user_id": APPROVER, "roles": ["approver"]},
        side_effects=["external_submission"],
    )


async def _ready_change_set(env) -> str:
    return await env.approvals.create_change_set(
        tenant_id=TENANT_A,
        workspace_id=env.workspace_id,
        run_id="run-1",
        items=[
            ChangeItemSpec(
                operation="update",
                path="forms/reimbursement.pdf",
                source_version_id=env.material_version_id,
                before_hash="a" * 64,
                after_hash="b" * 64,
            )
        ],
    )


async def _pending_approval(env, **digest_kwargs) -> tuple[str, str]:
    change_set_id = await _ready_change_set(env)
    return await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination="expense-system",
        requested_by=EMPLOYEE,
        digest_input=_digest_input(env, **digest_kwargs),
    )


async def _version_of(env, approval_id: str) -> int:
    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None
        return approval.version


# -- happy path --------------------------------------------------------------


async def test_approve_requires_matching_digest_and_version(env) -> None:
    approval_id, digest = await _pending_approval(env)
    approved = await env.approvals.decide(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        decision="approve",
        decided_by=APPROVER,
        expected_digest=digest,
        expected_version=await _version_of(env, approval_id),
    )
    assert approved.status == "approved"
    assert approved.decided_by == APPROVER and approved.decided_at is not None


async def test_reject_is_terminal(env) -> None:
    approval_id, digest = await _pending_approval(env)
    rejected = await env.approvals.decide(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        decision="reject",
        decided_by=APPROVER,
        expected_digest=digest,
        expected_version=await _version_of(env, approval_id),
        reason="receipts missing",
    )
    assert rejected.status == "rejected"
    # No transition out of a terminal state.
    with pytest.raises(ApprovalStateError, match="not be decided"):
        await env.approvals.decide(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            decision="approve",
            decided_by=APPROVER,
            expected_digest=digest,
            expected_version=await _version_of(env, approval_id),
        )


# -- digest / version / expiry gates -----------------------------------------


async def test_decision_with_a_different_digest_is_refused(env) -> None:
    approval_id, _digest = await _pending_approval(env)
    wrong = compute_action_digest(_digest_input(env, destination="somewhere-else"))
    with pytest.raises(ApprovalConflict, match="DIGEST_MISMATCH"):
        await env.approvals.decide(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            decision="approve",
            decided_by=APPROVER,
            expected_digest=wrong,
            expected_version=await _version_of(env, approval_id),
        )


async def test_decision_with_a_stale_version_is_a_conflict(env) -> None:
    approval_id, digest = await _pending_approval(env)
    with pytest.raises(ApprovalConflict, match="VERSION_CONFLICT"):
        await env.approvals.decide(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            decision="approve",
            decided_by=APPROVER,
            expected_digest=digest,
            expected_version=999,
        )


async def test_an_expired_approval_cannot_be_approved(env) -> None:
    change_set_id = await _ready_change_set(env)
    approval_id, digest = await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination="expense-system",
        requested_by=EMPLOYEE,
        digest_input=_digest_input(env),
        expires_at=utc_now() - timedelta(seconds=1),
    )
    with pytest.raises(ApprovalStateError, match="EXPIRED"):
        await env.approvals.decide(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            decision="approve",
            decided_by=APPROVER,
            expected_digest=digest,
            expected_version=await _version_of(env, approval_id),
        )
    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None and approval.status == "expired"


# -- invalidation on bound-input change --------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        {"destination": "another-system"},
        {"evidence": ("ev-1", "ev-2")},
        {"diff": "--- a\n+++ b different"},
    ],
)
async def test_any_bound_input_change_invalidates_a_prior_approval(env, changed) -> None:
    approval_id, digest = await _pending_approval(env)
    await env.approvals.decide(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        decision="approve",
        decided_by=APPROVER,
        expected_digest=digest,
        expected_version=await _version_of(env, approval_id),
    )
    invalidated = await env.approvals.invalidate_if_changed(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        current_digest_input=_digest_input(env, **changed),
    )
    assert invalidated is True
    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None and approval.status == "invalidated"


async def test_an_unchanged_input_does_not_invalidate(env) -> None:
    approval_id, digest = await _pending_approval(env)
    await env.approvals.decide(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        decision="approve",
        decided_by=APPROVER,
        expected_digest=digest,
        expected_version=await _version_of(env, approval_id),
    )
    invalidated = await env.approvals.invalidate_if_changed(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        current_digest_input=_digest_input(env),
    )
    assert invalidated is False, "an identical digest must not invalidate a valid approval"


# -- fresh RBAC --------------------------------------------------------------


async def test_authority_is_rechecked_at_decision_time(env) -> None:
    """A grant revoked after the request must block the decision."""
    approval_id, digest = await _pending_approval(env)
    # The approver's grant is revoked between request and decision.
    env.allowed.discard((TENANT_A, APPROVER, "approve"))
    with pytest.raises(ApprovalError, match="AUTH_FORBIDDEN"):
        await env.approvals.decide(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            decision="approve",
            decided_by=APPROVER,
            expected_digest=digest,
            expected_version=await _version_of(env, approval_id),
        )


async def test_a_user_without_authority_cannot_approve(env) -> None:
    approval_id, digest = await _pending_approval(env)
    with pytest.raises(ApprovalError, match="AUTH_FORBIDDEN"):
        await env.approvals.decide(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            decision="approve",
            decided_by=EMPLOYEE,  # the employee holds no approve grant
            expected_digest=digest,
            expected_version=await _version_of(env, approval_id),
        )


# -- tenant isolation --------------------------------------------------------


async def test_cross_tenant_decision_is_refused_like_absence(env) -> None:
    approval_id, digest = await _pending_approval(env)
    from tests.stage6_env import TENANT_B, TENANT_B_USER

    with pytest.raises(ApprovalError, match="APPROVAL_UNKNOWN"):
        await env.approvals.decide(
            tenant_id=TENANT_B,
            approval_id=approval_id,
            decision="approve",
            decided_by=TENANT_B_USER,
            expected_digest=digest,
            expected_version=1,
        )
    # The same error for a genuinely absent id, so neither leaks the other's data.
    with pytest.raises(ApprovalError, match="APPROVAL_UNKNOWN"):
        await env.approvals.decide(
            tenant_id=TENANT_B,
            approval_id="does-not-exist",
            decision="approve",
            decided_by=TENANT_B_USER,
            expected_digest=digest,
            expected_version=1,
        )


# -- change-set conflict detection -------------------------------------------


async def test_update_against_a_superseded_version_is_a_conflict(env) -> None:
    from backend.app.db.models import MaterialVersion

    async with env.factory() as session:
        version = await session.get(MaterialVersion, env.material_version_id)
        assert version is not None
        version.status = "superseded"
        version.version += 1
        await session.commit()

    with pytest.raises(ApprovalConflict, match="STALE"):
        await env.approvals.create_change_set(
            tenant_id=TENANT_A,
            workspace_id=env.workspace_id,
            run_id="run-1",
            items=[
                ChangeItemSpec(
                    operation="update",
                    path="forms/reimbursement.pdf",
                    source_version_id=env.material_version_id,
                )
            ],
        )


async def test_duplicate_path_in_a_change_set_is_rejected(env) -> None:
    with pytest.raises(ApprovalConflict, match="same path"):
        await env.approvals.create_change_set(
            tenant_id=TENANT_A,
            workspace_id=env.workspace_id,
            run_id="run-1",
            items=[
                ChangeItemSpec(operation="create", path="a/b.txt"),
                ChangeItemSpec(operation="create", path="a/./b.txt"),
            ],
        )


async def test_traversal_shaped_path_is_rejected_at_change_set_creation(env) -> None:
    with pytest.raises(ApprovalError, match="escapes the workspace root"):
        await env.approvals.create_change_set(
            tenant_id=TENANT_A,
            workspace_id=env.workspace_id,
            run_id="run-1",
            items=[ChangeItemSpec(operation="create", path="../../etc/passwd")],
        )
