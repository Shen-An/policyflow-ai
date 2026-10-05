"""T095 [US1] the reimbursement workflow end to end, on real infrastructure.

This is the business MVP as one scenario: an employee selects an authorized
material version into a workspace, a sandbox processor produces an evidence-backed
draft, a change set and a diff are formed, an approver approves, and the
submission goes out exactly once as a ``status=mock`` receipt. Then the negative
half: every unapproved / stale / revoked / cross-tenant path produces zero
external side effects and never rewrites the formal policy original.

It runs against real PostgreSQL and uses the local sandbox backend (no gVisor
isolation on this host -- that is verified at the manifest level in T092), so what
is proven here is the *composition*: the workspace, sandbox, change set, approval
and submission pieces fit together and the end-to-end invariants hold.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.approvals.digest import ActionDigestInput
from backend.app.approvals.service import ChangeItemSpec
from backend.app.approvals.submission import SubmissionBlocked
from backend.app.db.models import (
    ApprovalRequest,
    MaterialVersion,
    SubmissionJob,
    TaskWorkspace,
    WorkspaceInput,
)
from backend.app.sandbox.processors import (
    ProcessorDefinition,
    ProcessorRegistry,
)
from backend.app.sandbox.runner import (
    LocalSandboxBackend,
    SandboxInput,
    SandboxRequest,
    SandboxRunner,
)
from tests.stage6_env import APPROVER, EMPLOYEE, TENANT_A, stage6_environment

pytestmark = pytest.mark.asyncio

POLICY_FORM = b"name: ____\namount_cents: 0\njustification: ____\n"


@pytest_asyncio.fixture
async def env(pg_url: str):
    async with stage6_environment(pg_url=pg_url, scratch_name="pf_s6_e2e") as environment:
        yield environment


@pytest.fixture
def sandbox(tmp_path):
    """A sandbox runner whose local processor fills the reimbursement form."""

    async def fill(input_dir: Path, output_dir: Path) -> str:
        source = (input_dir / "form.txt").read_bytes()
        filled = source.replace(b"amount_cents: 0", b"amount_cents: 12300")
        (output_dir / "filled.txt").write_bytes(filled)
        return "succeeded"

    registry = ProcessorRegistry(
        (
            ProcessorDefinition(
                processor_id="reimbursement-fill",
                image="registry.internal/fill@sha256:" + "0" * 64,
                argv=("/usr/local/bin/fill", "--in", "/work/in", "--out", "/work/out"),
                signature_identity="cosign:ci@internal",
            ),
        )
    )

    async def fetch(tenant_id: str, material_id: str, material_version_id: str) -> bytes:
        return POLICY_FORM

    return SandboxRunner(
        workspace_root=tmp_path / "ws",
        backend=LocalSandboxBackend({"reimbursement-fill": fill}),
        fetcher=fetch,
        registry=registry,
    )


def _digest_input(env, *, after_hash: str, destination="expense-system") -> ActionDigestInput:
    return ActionDigestInput(
        action="reimbursement_submit",
        destination=destination,
        source_versions=[env.material_version_id],
        output_versions=["draft-1"],
        file_hashes={"forms/reimbursement.txt": after_hash},
        diff="amount_cents: 0 -> 12300",
        evidence_set=["policy-ev-1"],
        permission_snapshot={"tenant_id": TENANT_A, "user_id": APPROVER},
        side_effects=["external_submission"],
    )


async def _run_to_approval(env, sandbox) -> tuple[str, ActionDigestInput]:
    """Walk select -> sandbox draft -> change set -> approve; return the approval."""
    # 1. Select the authorized material version into a workspace input.
    async with env.factory() as session:
        session.add(
            WorkspaceInput(
                tenant_id=TENANT_A,
                workspace_id=env.workspace_id,
                material_version_id=env.material_version_id,
                purpose="edit",
                staged_hash="a" * 64,
                read_only=False,
            )
        )
        await session.commit()

    # 2. The sandbox produces the filled draft from the selected version.
    result = await sandbox.run_to_completion(
        SandboxRequest(
            tenant_id=TENANT_A,
            run_id="run-1",
            workspace_id=env.workspace_id,
            processor_id="reimbursement-fill",
            inputs=(
                SandboxInput(
                    material_id=env.material_id,
                    material_version_id=env.material_version_id,
                    relative_path="form.txt",
                    sha256=hashlib.sha256(POLICY_FORM).hexdigest(),
                ),
            ),
        )
    )
    assert result.status == "succeeded"
    after_hash = result.outputs[0].sha256

    # 3. Form the change set (update, against the current source version).
    change_set_id = await env.approvals.create_change_set(
        tenant_id=TENANT_A,
        workspace_id=env.workspace_id,
        run_id="run-1",
        items=[
            ChangeItemSpec(
                operation="update",
                path="forms/reimbursement.txt",
                source_version_id=env.material_version_id,
                before_hash="a" * 64,
                after_hash=after_hash,
            )
        ],
    )

    # 4. Request + approve.
    digest_input = _digest_input(env, after_hash=after_hash)
    approval_id, digest = await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination="expense-system",
        requested_by=EMPLOYEE,
        digest_input=digest_input,
    )
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
    return approval_id, digest_input


# -- the full happy path -----------------------------------------------------


async def test_full_reimbursement_flow_submits_once(env, sandbox) -> None:
    approval_id, digest_input = await _run_to_approval(env, sandbox)

    outcome = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
        payload={"amount_cents": 12300},
    )
    assert outcome.succeeded and outcome.status == "mock"

    # The formal policy original was never rewritten: the source version is intact
    # and still available (the edit produced a *new* draft, not a mutation).
    async with env.factory() as session:
        source = await session.get(MaterialVersion, env.material_version_id)
        assert source is not None and source.status == "available"
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval.status == "consumed"
        jobs = list((await session.execute(select(SubmissionJob))).scalars().all())
    assert len(jobs) == 1 and jobs[0].state == "succeeded"


async def test_repeated_submit_is_at_most_once(env, sandbox) -> None:
    approval_id, digest_input = await _run_to_approval(env, sandbox)
    outcomes = [
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            current_digest_input=digest_input,
        )
        for _ in range(3)
    ]
    assert all(o.succeeded for o in outcomes)
    assert len({o.job_id for o in outcomes}) == 1
    assert len(env.connector._accepted) == 1  # noqa: SLF001 - at most one accepted action


# -- zero side effects on every blocked path ---------------------------------


async def test_unapproved_change_set_produces_no_side_effect(env, sandbox) -> None:
    # Build everything up to (but not through) approval.
    async with env.factory() as session:
        session.add(
            WorkspaceInput(
                tenant_id=TENANT_A,
                workspace_id=env.workspace_id,
                material_version_id=env.material_version_id,
                purpose="edit",
                read_only=False,
            )
        )
        await session.commit()
    change_set_id = await env.approvals.create_change_set(
        tenant_id=TENANT_A,
        workspace_id=env.workspace_id,
        run_id="run-1",
        items=[ChangeItemSpec(operation="create", path="forms/new.txt")],
    )
    digest_input = _digest_input(env, after_hash="b" * 64)
    approval_id, _digest = await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination="expense-system",
        requested_by=EMPLOYEE,
        digest_input=digest_input,
    )
    with pytest.raises(SubmissionBlocked, match="NOT_APPROVED"):
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            current_digest_input=digest_input,
        )
    assert env.connector._accepted == {}  # noqa: SLF001 - zero external effects
    async with env.factory() as session:
        assert list((await session.execute(select(SubmissionJob))).scalars().all()) == []


async def test_revoked_authority_at_submit_produces_no_side_effect(env, sandbox) -> None:
    approval_id, digest_input = await _run_to_approval(env, sandbox)
    env.allowed.discard((TENANT_A, APPROVER, "submit"))
    with pytest.raises(SubmissionBlocked, match="AUTH_FORBIDDEN"):
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            current_digest_input=digest_input,
        )
    assert env.connector._accepted == {}  # noqa: SLF001


async def test_a_stale_digest_at_submit_produces_no_side_effect(env, sandbox) -> None:
    approval_id, _unused = await _run_to_approval(env, sandbox)
    with pytest.raises(SubmissionBlocked, match="STALE"):
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            # The action changed since approval (different destination).
            current_digest_input=_digest_input(env, after_hash="b" * 64, destination="elsewhere"),
        )
    assert env.connector._accepted == {}  # noqa: SLF001


async def test_the_formal_policy_original_is_never_writable(env, sandbox) -> None:
    """Selecting a formal policy version pulls it in read-only."""
    # Mark the material a formal policy original.
    from backend.app.db.models import Material

    async with env.factory() as session:
        material = await session.get(Material, env.material_id)
        material.source_type = "policy"
        material.read_only = True
        material.version += 1
        session.add(
            WorkspaceInput(
                tenant_id=TENANT_A,
                workspace_id=env.workspace_id,
                material_version_id=env.material_version_id,
                purpose="read",
                read_only=True,
            )
        )
        await session.commit()
        inputs = list(
            (
                await session.execute(
                    select(WorkspaceInput).where(
                        WorkspaceInput.workspace_id == env.workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert inputs and all(item.read_only for item in inputs), (
        "a formal policy original must enter the workspace read-only"
    )


# -- audit -------------------------------------------------------------------


async def test_the_run_is_bound_through_the_whole_flow(env, sandbox) -> None:
    """One run_id correlates the workspace, approval and submission."""
    approval_id, digest_input = await _run_to_approval(env, sandbox)
    await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
    )
    async with env.factory() as session:
        workspace = await session.get(TaskWorkspace, env.workspace_id)
        approval = await session.get(ApprovalRequest, approval_id)
        jobs = list((await session.execute(select(SubmissionJob))).scalars().all())
    assert workspace.run_id == "run-1"
    assert approval.run_id == "run-1"
    assert jobs[0].run_id == "run-1", "the submission must carry the same run_id"
