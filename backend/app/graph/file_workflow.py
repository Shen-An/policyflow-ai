"""T105 [US1] connect the graph's approval boundary to the real submission.

The graph runtime (``runtime.py``) already guarantees the two invariants that
matter for a high-impact action:

* **no side effect before an approved resume** -- a reject sets ``cancelled`` and
  never calls the side-effect executor;
* **exactly once after approval** -- a replayed approved resume returns the
  recorded receipt without touching the executor again.

What was missing was wiring that single ``execute(pending_action)`` boundary to
the *real* Stage-6 submission instead of the recording test double. This bridge is
that wire: it implements the graph's ``SideEffectExecutor`` protocol by calling
:class:`SubmissionService.submit_for_approval`, which re-authorizes (authority,
digest, target version) and is itself idempotent. So the graph decides *when* to
act (only post-approval, once), and the submission service decides *that it is
safe* to act (fresh re-auth) and *that it acts at most once* (unique key).

The pending action a file-workflow run carries into the interrupt names the
approval and the current digest inputs by value; nothing here reaches into a path,
an object key or a sandbox reference.
"""

from __future__ import annotations

from typing import Any

from backend.app.approvals.digest import ActionDigestInput
from backend.app.approvals.submission import SubmissionBlocked, SubmissionService


class FileWorkflowSideEffectExecutor:
    """Adapts :class:`SubmissionService` to the graph's ``SideEffectExecutor``.

    Returns a sanitized receipt dict (so the checkpoint stores no raw provider
    payload). A re-authorization failure is surfaced as a typed receipt rather
    than a raise: the graph has already committed to "approved and resuming", so a
    late authority revocation must be recorded as a blocked outcome, not crash the
    resume -- and crucially it still performs *no* submission.
    """

    def __init__(self, *, submissions: SubmissionService) -> None:
        self._submissions = submissions

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(action.get("tenant_id") or "")
        approval_id = str(action.get("approval_id") or "")
        destination = str(action.get("destination") or "")
        digest_input = _digest_input_from_action(action)
        if not tenant_id or not approval_id:
            raise ValueError("a file-workflow action must name a tenant and an approval")
        try:
            outcome = await self._submissions.submit_for_approval(
                tenant_id=tenant_id,
                approval_id=approval_id,
                destination=destination,
                current_digest_input=digest_input,
                payload=dict(action.get("payload") or {}),
            )
        except SubmissionBlocked as blocked:
            # Re-auth failed at execution: nothing was sent. Record it as a blocked
            # receipt so the run has an auditable, non-side-effecting outcome.
            return {
                "status": "blocked",
                "error_code": blocked.code,
                "submitted": False,
            }
        return {
            "status": outcome.status,
            "submitted": outcome.succeeded,
            "submission_id": outcome.job_id,
            "receipt_id": outcome.receipt_id,
            "deduplicated": outcome.deduplicated,
        }


def _digest_input_from_action(action: dict[str, Any]) -> ActionDigestInput:
    """Rebuild the digest input the approval was bound to from the pending action.

    The run carries these by value into the interrupt, so re-authorization at
    execution time compares against exactly what was approved.
    """
    digest = action.get("digest_input") or {}
    return ActionDigestInput(
        action=str(digest.get("action", action.get("action", ""))),
        destination=str(digest.get("destination", action.get("destination", ""))),
        source_versions=list(digest.get("source_versions", [])),
        output_versions=list(digest.get("output_versions", [])),
        file_hashes=dict(digest.get("file_hashes", {})),
        diff=str(digest.get("diff", "")),
        evidence_set=list(digest.get("evidence_set", [])),
        permission_snapshot=dict(digest.get("permission_snapshot", {})),
        side_effects=list(digest.get("side_effects", [])),
    )


__all__ = ["FileWorkflowSideEffectExecutor"]
