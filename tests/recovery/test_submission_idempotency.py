"""T094 [US1] submission idempotency: at most one accepted business action.

Runs against real PostgreSQL; skips cleanly when none is reachable. The whole
point of this suite is that no sequence of duplicate clicks, client retries or
worker restarts can produce two accepted submissions, and that an unknown provider
outcome is reconciled before anything is re-sent.

Proven here:

* a repeat ``submit_for_approval`` for the same approval returns the *same* job
  and never submits twice -- the approval is consumed exactly once, and the second
  call sees it already consumed;
* the mock connector is contacted at most once for a given key even across those
  repeats (its own dedup plus ours);
* a job left ``executing`` by a crash is resumed through ``reconciling`` -- it
  cannot jump straight back to ``ready`` -- and settles against the provider's
  real record;
* a submission whose approval is no longer ``approved`` (expired, invalidated,
  stale digest, revoked authority) is blocked before anything leaves the system;
* the unique ``(tenant_id, connector_id, idempotency_key)`` constraint holds under
  concurrent claims.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.approvals.digest import ActionDigestInput
from backend.app.approvals.service import ChangeItemSpec
from backend.app.approvals.submission import SubmissionBlocked, idempotency_key_for_approval
from backend.app.db.models import ApprovalRequest, SubmissionJob, utc_now
from tests.stage6_env import APPROVER, EMPLOYEE, TENANT_A, stage6_environment

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def env(pg_url: str):
    async with stage6_environment(pg_url=pg_url, scratch_name="pf_s6_submit") as environment:
        yield environment


def _digest_input(env, *, destination="expense-system") -> ActionDigestInput:
    return ActionDigestInput(
        action="reimbursement_submit",
        destination=destination,
        source_versions=[env.material_version_id],
        output_versions=["out-1"],
        file_hashes={"form.pdf": "a" * 64},
        diff="--- a",
        evidence_set=["ev-1"],
        permission_snapshot={"tenant_id": TENANT_A, "user_id": APPROVER},
        side_effects=["external_submission"],
    )


async def _approved(env, *, destination="expense-system") -> tuple[str, ActionDigestInput]:
    """Create an approved approval and return its id plus its digest input."""
    digest_input = _digest_input(env, destination=destination)
    change_set_id = await env.approvals.create_change_set(
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
    approval_id, digest = await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination=destination,
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


async def _jobs(env) -> list[SubmissionJob]:
    async with env.factory() as session:
        return list((await session.execute(select(SubmissionJob))).scalars().all())


# -- happy path --------------------------------------------------------------


async def test_a_submission_succeeds_and_consumes_the_approval(env) -> None:
    approval_id, digest_input = await _approved(env)
    outcome = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
        payload={"amount_cents": 12300},
    )
    assert outcome.succeeded
    assert outcome.status == "mock", "a mock submission must be unmistakably mock"
    assert outcome.receipt_id

    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None and approval.status == "consumed", (
            "a successful submission must consume its approval"
        )
    assert len(await _jobs(env)) == 1


# -- idempotency -------------------------------------------------------------


async def test_duplicate_click_returns_the_same_job_and_submits_once(env) -> None:
    approval_id, digest_input = await _approved(env)
    first = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
    )
    # The user clicks "submit" again (and again): same approval, same key.
    second = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
    )
    third = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
    )
    assert first.job_id == second.job_id == third.job_id
    assert all(outcome.succeeded for outcome in (first, second, third))
    assert len(await _jobs(env)) == 1, "duplicate clicks must not create a second job"
    # The provider accepted exactly one submission for this key.
    assert len(env.connector._accepted) == 1  # noqa: SLF001 - asserting dedup state


async def test_concurrent_claims_create_exactly_one_job(env) -> None:
    """Two simultaneous submits race; the unique key lets only one job exist."""
    approval_id, digest_input = await _approved(env)
    results = await asyncio.gather(
        *(
            env.submissions.submit_for_approval(
                tenant_id=TENANT_A,
                approval_id=approval_id,
                destination="expense-system",
                current_digest_input=digest_input,
            )
            for _ in range(5)
        ),
        return_exceptions=True,
    )
    outcomes = [r for r in results if not isinstance(r, Exception)]
    assert outcomes, f"all concurrent claims failed: {results}"
    job_ids = {outcome.job_id for outcome in outcomes}
    assert len(job_ids) == 1, f"concurrent claims created {len(job_ids)} jobs"
    assert len(await _jobs(env)) == 1


async def test_submission_key_is_derived_from_the_approval(env) -> None:
    """The idempotency key is a function of the approval, so retries collide."""
    approval_id, digest_input = await _approved(env)
    await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
    )
    jobs = await _jobs(env)
    assert jobs[0].idempotency_key == idempotency_key_for_approval(approval_id)


# -- unknown outcome / worker restart ----------------------------------------


async def test_interrupted_job_reconciles_before_resuming(env) -> None:
    """A job left executing by a crash must reconcile, not jump back to ready.

    The provider actually accepted it (its dedup record exists), so reconciliation
    settles the job as succeeded against the known receipt rather than re-sending.
    """
    approval_id, digest_input = await _approved(env)
    key = idempotency_key_for_approval(approval_id)

    # Simulate: the approval was consumed and the job created+left executing, and
    # the provider had in fact accepted the submission, but the process crashed
    # before recording success.
    await env.connector.submit(
        tenant_id=TENANT_A, idempotency_key=key, destination="expense-system", payload={}
    )
    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        approval.status = "consumed"
        approval.version += 1
        session.add(
            SubmissionJob(
                tenant_id=TENANT_A,
                run_id="run-1",
                approval_id=approval_id,
                connector_id=env.connector.connector_id,
                destination="expense-system",
                idempotency_key=key,
                state="executing",
                attempts=1,
            )
        )
        await session.commit()

    outcome = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
    )
    assert outcome.succeeded, "reconciliation must settle an accepted submission"
    async with env.factory() as session:
        jobs = list((await session.execute(select(SubmissionJob))).scalars().all())
    assert len(jobs) == 1 and jobs[0].state == "succeeded"
    # The provider still has exactly one accepted record: no double-send.
    assert len(env.connector._accepted) == 1  # noqa: SLF001


async def test_reconcile_resumes_a_truly_unsent_job(env) -> None:
    """An unknown-outcome job the provider never saw is safely re-sent once."""
    approval_id, digest_input = await _approved(env)
    key = idempotency_key_for_approval(approval_id)
    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        approval.status = "consumed"
        approval.version += 1
        session.add(
            SubmissionJob(
                tenant_id=TENANT_A,
                run_id="run-1",
                approval_id=approval_id,
                connector_id=env.connector.connector_id,
                destination="expense-system",
                idempotency_key=key,
                state="unknown_outcome",
                attempts=1,
            )
        )
        await session.commit()

    outcome = await env.submissions.reconcile(tenant_id=TENANT_A, job_id=(await _jobs(env))[0].id)
    assert outcome.succeeded
    assert len(env.connector._accepted) == 1, "the job was sent exactly once after reconcile"


# -- blocked before any side effect ------------------------------------------


async def test_expired_approval_blocks_submission(env) -> None:
    approval_id, digest_input = await _approved(env)
    async with env.factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        approval.expires_at = utc_now()
        approval.version += 1
        await session.commit()
    with pytest.raises(SubmissionBlocked, match="EXPIRED"):
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            current_digest_input=digest_input,
        )
    assert env.connector._accepted == {}, "nothing may be sent for an expired approval"  # noqa: SLF001


async def test_stale_digest_blocks_submission(env) -> None:
    """If the action changed since approval, submission is blocked (fresh digest)."""
    approval_id, _unused = await _approved(env)
    with pytest.raises(SubmissionBlocked, match="STALE"):
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            # A different destination => different digest => stale approval.
            current_digest_input=_digest_input(env, destination="somewhere-else"),
        )
    assert env.connector._accepted == {}  # noqa: SLF001


async def test_revoked_authority_blocks_submission(env) -> None:
    """Authority is rechecked at submit time, not taken from the approval."""
    approval_id, digest_input = await _approved(env)
    env.allowed.discard((TENANT_A, APPROVER, "submit"))
    with pytest.raises(SubmissionBlocked, match="AUTH_FORBIDDEN"):
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            current_digest_input=digest_input,
        )
    assert env.connector._accepted == {}  # noqa: SLF001


async def test_unapproved_change_set_cannot_be_submitted(env) -> None:
    """A pending (never approved) change set has nothing to consume."""
    digest_input = _digest_input(env)
    change_set_id = await env.approvals.create_change_set(
        tenant_id=TENANT_A,
        workspace_id=env.workspace_id,
        run_id="run-1",
        items=[ChangeItemSpec(operation="create", path="forms/new.pdf")],
    )
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
    assert await _jobs(env) == []
