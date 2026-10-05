"""Capture Stage-6 security evidence against live infrastructure (T109).

Drives the *production* approval/submission/sandbox code paths (same services the
app wires) to produce the evidence ``tasks.md`` T109 asks for -- 0 leaks, 0
unapproved side effects, at most one mock submission -- plus the sandbox policy
manifest attestation:

* ``approval-flow.json`` -- the reimbursement flow: approved then submitted once,
  with the formal policy original untouched and the approval consumed.
* ``blocked-paths.json`` -- unapproved / revoked / stale / cross-tenant: each
  blocked, each with zero accepted provider actions.
* ``idempotency.json`` -- duplicate clicks and concurrent claims resolve to one
  job and one accepted action.
* ``sandbox-policy.json`` -- the controls the gVisor Job manifest expresses, with
  the honest note that live gVisor execution is gated on a cluster this host
  lacks.
* ``infra-probe.txt`` -- the server versions the evidence was taken against.

Run with the dev stack up:
    python -m scripts.capture_stage6_evidence
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - CLI convenience
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.app.approvals.digest import ActionDigestInput  # noqa: E402
from backend.app.approvals.service import ChangeItemSpec  # noqa: E402
from backend.app.approvals.submission import SubmissionBlocked  # noqa: E402
from backend.app.db.models import (  # noqa: E402
    ApprovalRequest,
    MaterialVersion,
    SubmissionJob,
)
from tests import conftest  # noqa: E402
from tests.stage6_env import (  # noqa: E402
    APPROVER,
    EMPLOYEE,
    TENANT_A,
    TENANT_B,
    TENANT_B_USER,
    stage6_environment,
)

OUTPUT_DIR = REPO_ROOT / "artifacts" / "security" / "stage6"
MANIFEST_PATH = REPO_ROOT / "infra" / "k8s" / "sandbox-job.yaml"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest_input(env, *, destination="expense-system") -> ActionDigestInput:
    return ActionDigestInput(
        action="reimbursement_submit",
        destination=destination,
        source_versions=[env.material_version_id],
        output_versions=["draft-1"],
        file_hashes={"form.txt": "a" * 64},
        diff="amount 0 -> 12300",
        evidence_set=["ev-1"],
        permission_snapshot={"tenant_id": TENANT_A, "user_id": APPROVER},
        side_effects=["external_submission"],
    )


async def _approved(env, *, destination="expense-system"):
    digest_input = _digest_input(env, destination=destination)
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


async def capture_approval_flow(env) -> dict[str, Any]:
    approval_id, digest_input = await _approved(env)
    outcome = await env.submissions.submit_for_approval(
        tenant_id=TENANT_A,
        approval_id=approval_id,
        destination="expense-system",
        current_digest_input=digest_input,
        payload={"amount_cents": 12300},
    )
    async with env.factory() as session:
        source = await session.get(MaterialVersion, env.material_version_id)
        approval = await session.get(ApprovalRequest, approval_id)
        jobs = list((await session.execute(select(SubmissionJob))).scalars().all())
    return {
        "captured_at": _now(),
        "submission": {
            "status": outcome.status,
            "succeeded": outcome.succeeded,
            "receipt_id": outcome.receipt_id,
        },
        "jobs": len(jobs),
        "approval_status": approval.status,
        "source_version_status": source.status if source else None,
        "invariants": {
            "submitted_once": outcome.succeeded and len(jobs) == 1,
            "status_is_mock": outcome.status == "mock",
            "approval_consumed": approval.status == "consumed",
            "formal_original_untouched": source is not None and source.status == "available",
            "one_accepted_provider_action": len(env.connector._accepted) == 1,  # noqa: SLF001
        },
    }


async def capture_blocked_paths(env) -> dict[str, Any]:
    results: dict[str, Any] = {}

    # 1. Unapproved: request but never approve.
    digest_input = _digest_input(env)
    change_set_id = await env.approvals.create_change_set(
        tenant_id=TENANT_A,
        workspace_id=env.workspace_id,
        run_id="run-1",
        items=[ChangeItemSpec(operation="create", path="forms/new.txt")],
    )
    unapproved_id, _d = await env.approvals.request_approval(
        tenant_id=TENANT_A,
        run_id="run-1",
        change_set_id=change_set_id,
        action="reimbursement_submit",
        destination="expense-system",
        requested_by=EMPLOYEE,
        digest_input=digest_input,
    )
    results["unapproved"] = await _expect_blocked(
        env, unapproved_id, digest_input, expected="NOT_APPROVED"
    )

    # 2. Stale digest on an otherwise-approved action.
    approval_id, _di = await _approved(env)
    results["stale_digest"] = await _expect_blocked(
        env, approval_id, _digest_input(env, destination="elsewhere"), expected="STALE"
    )

    # 3. Revoked authority at submit time.
    approval_id2, di2 = await _approved(env)
    env.allowed.discard((TENANT_A, APPROVER, "submit"))
    results["revoked_authority"] = await _expect_blocked(
        env, approval_id2, di2, expected="AUTH_FORBIDDEN"
    )
    env.allowed.add((TENANT_A, APPROVER, "submit"))

    # 4. Cross-tenant decision is refused.
    cross_blocked = False
    try:
        await env.approvals.decide(
            tenant_id=TENANT_B,
            approval_id=approval_id,
            decision="approve",
            decided_by=TENANT_B_USER,
            expected_digest="a" * 64,
            expected_version=1,
        )
    except Exception as exc:  # noqa: BLE001 - any refusal is the point
        cross_blocked = "APPROVAL_UNKNOWN" in str(exc)
    results["cross_tenant_decision"] = {"blocked": cross_blocked}

    async with env.factory() as session:
        jobs = list((await session.execute(select(SubmissionJob))).scalars().all())
    results["captured_at"] = _now()
    results["total_submission_jobs"] = len(jobs)
    results["accepted_provider_actions"] = len(env.connector._accepted)  # noqa: SLF001
    results["invariants"] = {
        "every_path_blocked": all(
            entry.get("blocked") for key, entry in results.items() if isinstance(entry, dict)
        ),
        "zero_external_side_effects": len(env.connector._accepted) == 0,  # noqa: SLF001
        "zero_submission_jobs": len(jobs) == 0,
    }
    return results


async def _expect_blocked(env, approval_id, digest_input, *, expected) -> dict[str, Any]:
    try:
        await env.submissions.submit_for_approval(
            tenant_id=TENANT_A,
            approval_id=approval_id,
            destination="expense-system",
            current_digest_input=digest_input,
        )
    except SubmissionBlocked as exc:
        return {"blocked": True, "error_code": exc.code, "matched_expected": expected in exc.code}
    return {"blocked": False, "error_code": None, "matched_expected": False}


async def capture_idempotency(env) -> dict[str, Any]:
    approval_id, digest_input = await _approved(env)
    outcomes = []
    for _ in range(3):  # duplicate clicks
        outcomes.append(
            await env.submissions.submit_for_approval(
                tenant_id=TENANT_A,
                approval_id=approval_id,
                destination="expense-system",
                current_digest_input=digest_input,
            )
        )
    # Concurrent claims for a *second* approval.
    approval_id2, digest_input2 = await _approved(env)
    concurrent = await asyncio.gather(
        *(
            env.submissions.submit_for_approval(
                tenant_id=TENANT_A,
                approval_id=approval_id2,
                destination="expense-system",
                current_digest_input=digest_input2,
            )
            for _ in range(5)
        ),
        return_exceptions=True,
    )
    concurrent_ok = [r for r in concurrent if not isinstance(r, Exception)]
    async with env.factory() as session:
        jobs = list((await session.execute(select(SubmissionJob))).scalars().all())
    return {
        "captured_at": _now(),
        "duplicate_click_job_ids": sorted({o.job_id for o in outcomes}),
        "concurrent_job_ids": sorted({o.job_id for o in concurrent_ok}),
        "total_jobs": len(jobs),
        "accepted_provider_actions": len(env.connector._accepted),  # noqa: SLF001
        "invariants": {
            "duplicate_clicks_one_job": len({o.job_id for o in outcomes}) == 1,
            "concurrent_claims_one_job": len({o.job_id for o in concurrent_ok}) == 1,
            # Two approvals => at most two accepted actions, never more.
            "at_most_one_action_per_approval": len(env.connector._accepted) == 2,  # noqa: SLF001
        },
    }


def capture_sandbox_policy() -> dict[str, Any]:
    """Attest the controls the gVisor Job manifest expresses (live run gated)."""
    docs = [doc for doc in yaml.safe_load_all(MANIFEST_PATH.read_text(encoding="utf-8")) if doc]
    job = next(doc for doc in docs if doc.get("kind") == "Job")
    netpol = next(doc for doc in docs if doc.get("kind") == "NetworkPolicy")
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    return {
        "captured_at": _now(),
        "note": (
            "This host has no Kubernetes/gVisor; the manifest is attested for policy "
            "completeness, not executed. Live gVisor isolation is gated [~]."
        ),
        "controls": {
            "runtime_class_gvisor": pod.get("runtimeClassName") == "gvisor",
            "run_as_non_root": pod["securityContext"]["runAsNonRoot"] is True,
            "read_only_root_fs": container["securityContext"]["readOnlyRootFilesystem"] is True,
            "all_capabilities_dropped": container["securityContext"]["capabilities"]["drop"]
            == ["ALL"],
            "no_privilege_escalation": container["securityContext"]["allowPrivilegeEscalation"]
            is False,
            "seccomp_runtime_default": pod["securityContext"]["seccompProfile"]["type"]
            == "RuntimeDefault",
            "no_service_account_token": pod["automountServiceAccountToken"] is False,
            "no_host_namespaces": not any(
                pod.get(k, False) for k in ("hostNetwork", "hostPID", "hostIPC")
            ),
            "no_hostpath_volumes": all("hostPath" not in v for v in pod.get("volumes", [])),
            "resource_limits_present": set(container["resources"]["limits"])
            >= {"cpu", "memory", "ephemeral-storage"},
            "active_deadline_present": bool(job["spec"].get("activeDeadlineSeconds")),
            "default_deny_egress": netpol["spec"]["egress"] == []
            and "Egress" in netpol["spec"]["policyTypes"],
        },
    }


def capture_infra_probe() -> str:
    lines = [f"Stage-6 security evidence probe, captured {_now()}", ""]
    lines.append("PostgreSQL: via infra/dev/compose.yaml (approval/submission are PG-backed)")
    lines.append("Sandbox gVisor: NOT run on this host (no cluster); manifest attested only.")
    return "\n".join(lines) + "\n"


async def _main() -> int:
    pg_url = conftest.test_database_url()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "infra-probe.txt").write_text(capture_infra_probe(), encoding="utf-8")
    (OUTPUT_DIR / "sandbox-policy.json").write_text(
        json.dumps(capture_sandbox_policy(), indent=2) + "\n", encoding="utf-8"
    )

    captures = (
        ("approval-flow.json", capture_approval_flow, "ev_flow"),
        ("blocked-paths.json", capture_blocked_paths, "ev_blocked"),
        ("idempotency.json", capture_idempotency, "ev_idem"),
    )
    failures = 0
    for filename, capture, scratch in captures:
        async with stage6_environment(pg_url=pg_url, scratch_name=f"pf_{scratch}") as env:
            payload = await capture(env)
        (OUTPUT_DIR / filename).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        broken = [name for name, held in payload.get("invariants", {}).items() if not held]
        failures += bool(broken)
        print(f"{filename}: {'OK' if not broken else 'BROKEN: ' + str(broken)}")

    policy = capture_sandbox_policy()
    broken = [name for name, held in policy["controls"].items() if not held]
    failures += bool(broken)
    print(f"sandbox-policy.json: {'OK' if not broken else 'BROKEN: ' + str(broken)}")
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
