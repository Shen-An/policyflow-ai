"""T102 [US1] the ChangeSet + ApprovalRequest service: create, decide, invalidate.

This owns the approval half of the business MVP. Its guarantees:

* **A change set is built with conflict detection.** An edit names the source
  version it was computed against; if the material's current version has moved on,
  that is a conflict, never a silent overwrite (``data-model.md``). Paths are
  normalised and unique within the workspace.
* **An approval binds a digest.** Requesting approval computes the action digest
  (T101) over the change set's inputs and stores it. A decision must present the
  digest it reviewed; a mismatch is rejected, so an approval granted for one
  action can never execute a different one.
* **Any bound-input change invalidates a prior approval.** When the change set,
  its evidence or the permission snapshot changes, the recomputed digest differs
  and :meth:`invalidate_if_changed` moves the approval to ``invalidated``.
* **The state machine is the one in the model.** ``pending`` is the only state a
  decision is made from; transitions are compare-and-set on ``version`` so two
  concurrent decisions cannot both win.
* **Authority is rechecked at decision time.** The approver must currently hold
  the required grant -- a snapshot is not enough -- which is supplied by an
  injected check so this service does not reach into the full principal stack.

Consumption (``approved -> consumed``) is deliberately *not* here: it happens
atomically with claiming the submission, in ``approvals/submission.py``, so an
approval and its one submission are created together or not at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.approvals.digest import (
    ActionDigestInput,
    compute_action_digest,
    digests_match,
)
from backend.app.db.models import (
    APPROVAL_TRANSITIONS,
    ApprovalRequest,
    ChangeSet,
    ChangeSetItem,
    MaterialVersion,
    change_item_source_error,
    utc_now,
)

#: An injected check that answers "does this user, right now, hold the authority
#: to decide/execute this action?". Returns None when allowed, or a reason string
#: when denied. Injected rather than importing the authorization service so the
#: approval logic is testable in isolation and so a revocation lands immediately
#: (the check re-reads current grants).
AuthorityCheck = Callable[[str, str, str], Awaitable[str | None]]


class ApprovalError(RuntimeError):
    """A base for approval-flow failures carrying a stable short code."""

    def __init__(self, code: str, message: str) -> None:
        # The stable code is part of the string so callers (and tests) can assert
        # on it without a separate attribute read, and logs carry it inline.
        super().__init__(f"{code}: {message}")
        self.code = code


class ApprovalStateError(ApprovalError):
    """A transition was requested from a state that forbids it."""


class ApprovalConflict(ApprovalError):  # noqa: N818 - reads as the condition
    """An expected version or digest did not match the current one."""


@dataclass(frozen=True)
class ChangeItemSpec:
    """One proposed edit, as the caller describes it before persistence."""

    operation: str
    path: str
    source_version_id: str | None = None
    proposed_version_id: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    diff_artifact_ref: str | None = None


def normalize_path(path: str) -> str:
    """Normalise a workspace-relative path for persistence and uniqueness.

    Pure string normalisation of separators and ``.`` segments only -- it does
    *not* decide containment (that is ``sandbox/validation.py`` against a real
    realpath). Its job is to make "the same file" one canonical string so the
    per-workspace unique constraint actually catches duplicates: ``a/b.txt`` and
    ``a/./b.txt`` must collide. A ``..`` segment is preserved here (not resolved
    away) so a traversal attempt is stored verbatim and rejected downstream rather
    than being silently canonicalised into something that looks safe.
    """
    cleaned = path.replace("\\", "/").strip()
    segments: list[str] = []
    for segment in cleaned.split("/"):
        if segment in ("", "."):
            continue
        segments.append(segment)
    return "/".join(segments)


class ApprovalService:
    """ChangeSet + ApprovalRequest orchestration over an async session factory."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        authority_check: AuthorityCheck | None = None,
    ) -> None:
        self._factory = factory
        self._authority_check = authority_check

    # -- change sets ----------------------------------------------------------

    async def create_change_set(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        items: Sequence[ChangeItemSpec],
        summary: str = "",
        side_effect_class: str = "external_submission",
        source_manifest_digest: str = "",
        evidence_set_digest: str = "",
    ) -> str:
        """Create a change set and its items, detecting conflicts.

        A conflict is raised (never a silent overwrite) when an update/delete names
        a source version that is no longer the material's current version, matching
        ``data-model.md``: concurrent edits require the expected source version.
        """
        for spec in items:
            reason = change_item_source_error(
                operation=spec.operation, source_version_id=spec.source_version_id
            )
            if reason is not None:
                raise ApprovalError("CHANGE_ITEM_INVALID", reason)

        normalized = [normalize_path(spec.path) for spec in items]
        if any(not path or path.startswith("..") or "/.." in path for path in normalized):
            # A traversal-shaped path is refused here as a cheap first gate; the
            # realpath containment check in the sandbox is the authoritative one.
            raise ApprovalError(
                "CHANGE_PATH_INVALID", "a change path escapes the workspace root"
            )
        if len(set(normalized)) != len(normalized):
            raise ApprovalConflict(
                "CHANGE_PATH_DUPLICATE", "two change items target the same path"
            )

        async with self._factory() as session:
            await self._assert_source_versions_current(session, tenant_id, items)
            change_set = ChangeSet(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=run_id,
                summary=summary,
                side_effect_class=side_effect_class,
                source_manifest_digest=source_manifest_digest,
                evidence_set_digest=evidence_set_digest,
                state="ready",
            )
            session.add(change_set)
            await session.flush()
            for spec, path in zip(items, normalized, strict=True):
                session.add(
                    ChangeSetItem(
                        tenant_id=tenant_id,
                        change_set_id=change_set.id,
                        workspace_id=workspace_id,
                        source_version_id=spec.source_version_id,
                        proposed_version_id=spec.proposed_version_id,
                        operation=spec.operation,
                        normalized_path=path,
                        before_hash=spec.before_hash,
                        after_hash=spec.after_hash,
                        diff_artifact_ref=spec.diff_artifact_ref,
                    )
                )
            await session.commit()
            return change_set.id

    async def _assert_source_versions_current(
        self, session: AsyncSession, tenant_id: str, items: Sequence[ChangeItemSpec]
    ) -> None:
        for spec in items:
            if spec.source_version_id is None:
                continue
            version = await session.get(MaterialVersion, spec.source_version_id)
            if version is None or version.tenant_id != tenant_id:
                raise ApprovalConflict(
                    "SOURCE_VERSION_UNKNOWN",
                    "the change set references a source version that does not exist",
                )
            if version.status == "superseded":
                raise ApprovalConflict(
                    "SOURCE_VERSION_STALE",
                    "the source version has been superseded; recompute the change "
                    "against the current version",
                )

    # -- approvals ------------------------------------------------------------

    async def request_approval(
        self,
        *,
        tenant_id: str,
        run_id: str,
        change_set_id: str,
        action: str,
        destination: str,
        requested_by: str,
        digest_input: ActionDigestInput,
        expires_at: datetime | None = None,
        authorization_version: int = 1,
    ) -> tuple[str, str]:
        """Create a pending approval bound to the change set's action digest.

        Returns ``(approval_id, action_digest)``. The change set moves to
        ``awaiting_approval`` in the same transaction, so a change set cannot have
        two live approvals racing.
        """
        action_digest = compute_action_digest(digest_input)
        async with self._factory() as session:
            change_set = await session.get(ChangeSet, change_set_id)
            if change_set is None or change_set.tenant_id != tenant_id:
                raise ApprovalError("CHANGE_SET_UNKNOWN", "no such change set for this tenant")
            if change_set.state not in {"ready", "draft"}:
                raise ApprovalStateError(
                    "CHANGE_SET_NOT_READY",
                    f"a change set in {change_set.state} cannot request approval",
                )
            approval = ApprovalRequest(
                tenant_id=tenant_id,
                run_id=run_id,
                change_set_id=change_set_id,
                action=action,
                destination=destination,
                action_digest=action_digest,
                requested_by=requested_by,
                authorization_version=authorization_version,
                status="pending",
                expires_at=expires_at,
            )
            session.add(approval)
            await self._cas_change_set(session, change_set, state="awaiting_approval")
            await session.commit()
            return approval.id, action_digest

    async def decide(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        decision: str,
        decided_by: str,
        expected_digest: str,
        expected_version: int,
        reason: str | None = None,
        required_authority: str = "approve",
    ) -> ApprovalRequest:
        """Approve or reject a pending approval.

        Every guard that could let an approval execute something other than what
        was reviewed is checked here: the approval must be ``pending`` and
        unexpired, the presented digest must match, the presented version must
        match (optimistic concurrency on what the approver saw), and the approver
        must *currently* hold the authority -- a stale snapshot is not enough.
        """
        if decision not in {"approve", "reject"}:
            raise ApprovalError("DECISION_INVALID", "decision must be approve or reject")
        target = "approved" if decision == "approve" else "rejected"

        await self._require_authority(tenant_id, decided_by, required_authority)

        async with self._factory() as session:
            approval = await self._load(session, tenant_id, approval_id)
            if approval.version != expected_version:
                raise ApprovalConflict(
                    "APPROVAL_VERSION_CONFLICT",
                    "the approval changed since it was reviewed; reload and decide again",
                )
            if approval.status != "pending":
                raise ApprovalStateError(
                    "APPROVAL_NOT_PENDING",
                    f"an approval in {approval.status} cannot be decided",
                )
            if self._is_expired(approval):
                await self._cas_approval(session, approval, status="expired")
                await session.commit()
                raise ApprovalStateError("APPROVAL_EXPIRED", "the approval has expired")
            if not digests_match(approval.action_digest, expected_digest):
                raise ApprovalConflict(
                    "APPROVAL_DIGEST_MISMATCH",
                    "the reviewed action no longer matches; the request has changed",
                )
            _assert_transition(approval.status, target)
            await self._cas_approval(
                session,
                approval,
                status=target,
                decided_by=decided_by,
                decided_at=utc_now(),
                reason=reason,
            )
            if target == "rejected":
                change_set = await session.get(ChangeSet, approval.change_set_id)
                if change_set is not None:
                    await self._cas_change_set(session, change_set, state="rejected")
            else:
                change_set = await session.get(ChangeSet, approval.change_set_id)
                if change_set is not None:
                    await self._cas_change_set(session, change_set, state="approved")
            await session.commit()
            await session.refresh(approval)
            return approval

    async def invalidate_if_changed(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        current_digest_input: ActionDigestInput,
    ) -> bool:
        """Invalidate the approval when its bound inputs have changed.

        Returns True when it invalidated. Called whenever something the digest
        binds (the change set, its evidence, the permission snapshot) is edited:
        any such change recomputes to a different digest, and an approval that no
        longer matches what it approved must not be executable.
        """
        current_digest = compute_action_digest(current_digest_input)
        async with self._factory() as session:
            approval = await self._load(session, tenant_id, approval_id)
            if approval.status not in {"pending", "approved"}:
                return False
            if digests_match(approval.action_digest, current_digest):
                return False
            _assert_transition(approval.status, "invalidated")
            await self._cas_approval(session, approval, status="invalidated")
            change_set = await session.get(ChangeSet, approval.change_set_id)
            if change_set is not None and change_set.state in {"awaiting_approval", "approved"}:
                await self._cas_change_set(session, change_set, state="invalidated")
            await session.commit()
            return True

    async def expire_due(self, *, tenant_id: str, now: datetime | None = None) -> int:
        """Expire every pending/approved approval past its deadline. Idempotent."""
        moment = now or utc_now()
        expired = 0
        async with self._factory() as session:
            rows = await session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.status.in_(("pending", "approved")),
                    ApprovalRequest.expires_at.is_not(None),
                    ApprovalRequest.expires_at < moment,
                )
            )
            for approval in rows.scalars().all():
                await self._cas_approval(session, approval, status="expired")
                expired += 1
            await session.commit()
        return expired

    # -- helpers --------------------------------------------------------------

    async def _require_authority(
        self, tenant_id: str, user_id: str, action: str
    ) -> None:
        if self._authority_check is None:
            return
        reason = await self._authority_check(tenant_id, user_id, action)
        if reason is not None:
            raise ApprovalError("AUTH_FORBIDDEN", reason)

    def _is_expired(self, approval: ApprovalRequest) -> bool:
        return approval.expires_at is not None and approval.expires_at < utc_now()

    async def _load(
        self, session: AsyncSession, tenant_id: str, approval_id: str
    ) -> ApprovalRequest:
        approval = await session.get(ApprovalRequest, approval_id)
        if approval is None or approval.tenant_id != tenant_id:
            raise ApprovalError("APPROVAL_UNKNOWN", "no such approval for this tenant")
        return approval

    async def _cas_approval(
        self, session: AsyncSession, approval: ApprovalRequest, **values: Any
    ) -> None:
        expected = approval.version
        moment = utc_now()
        result = await session.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval.id,
                ApprovalRequest.version == expected,
            )
            .values(version=expected + 1, updated_at=moment, **values)
        )
        if result.rowcount != 1:
            raise ApprovalConflict(
                "APPROVAL_VERSION_CONFLICT",
                f"concurrent modification of approval {approval.id}",
            )
        for key, value in values.items():
            setattr(approval, key, value)
        approval.version = expected + 1
        approval.updated_at = moment

    async def _cas_change_set(
        self, session: AsyncSession, change_set: ChangeSet, **values: Any
    ) -> None:
        expected = change_set.version
        result = await session.execute(
            update(ChangeSet)
            .where(ChangeSet.id == change_set.id, ChangeSet.version == expected)
            .values(version=expected + 1, updated_at=utc_now(), **values)
        )
        if result.rowcount != 1:
            raise ApprovalConflict(
                "CHANGE_SET_VERSION_CONFLICT",
                f"concurrent modification of change set {change_set.id}",
            )
        for key, value in values.items():
            setattr(change_set, key, value)
        change_set.version = expected + 1


def _assert_transition(from_status: str, to_status: str) -> None:
    allowed = APPROVAL_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise ApprovalStateError(
            "APPROVAL_TRANSITION_INVALID",
            f"{from_status} -> {to_status} is not a legal approval transition",
        )
