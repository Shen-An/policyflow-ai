"""T106 [US1] ``POST /api/v2/runs/{run_id}/approvals/{approval_id}``: decide an approval.

The decision endpoint is deliberately small and strict. The body carries only a
decision, the digest the approver reviewed, the version they reviewed, and an
optional reason -- and each is validated at the edge:

* ``decision`` is ``approve`` or ``reject`` (nothing else);
* ``action_digest`` must be 64-char lowercase hex, so a malformed digest is a 422
  before any state is touched;
* ``expected_version`` is ``>= 1`` (optimistic concurrency on what was reviewed);
* ``reason`` is at most 1000 chars.

Everything that makes the decision *safe* -- the digest actually matching, the
version actually matching, the approval still pending and unexpired, and a fresh
authority recheck -- lives in :class:`ApprovalService`; this route maps its typed
failures onto the HTTP contract (409 for a conflict, 403 for authority, 404 for an
unknown/foreign approval) and never leaks another tenant's data.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.api.deps import PrincipalDep
from backend.app.approvals.service import (
    ApprovalConflict,
    ApprovalError,
    ApprovalService,
    ApprovalStateError,
)
from backend.app.core.exceptions import ApplicationError

router = APIRouter(prefix="/api/v2", tags=["v2", "approvals"])

_SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")

#: Scopes/roles that confer approval authority. The recheck is against the live
#: principal, so a revoked grant is reflected immediately.
_APPROVE_SCOPES = frozenset({"approval:decide", "approval:manage", "policy:manage"})
_APPROVE_ROLES = frozenset({"admin", "approver", "policy_admin"})


class ApprovalDecisionRequest(BaseModel):
    """Body for the decision endpoint. Tenant/user come from the token only."""

    decision: Literal["approve", "reject"]
    action_digest: str = Field(min_length=64, max_length=64)
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("action_digest")
    @classmethod
    def _lowercase_hex(cls, value: str) -> str:
        if not _SHA256_HEX.match(value):
            raise ValueError("action_digest must be 64-char lowercase hex")
        return value


def _session_factory(request: Request) -> async_sessionmaker:
    override = getattr(request.app.state, "approval_service", None)
    if override is not None:
        return override._factory  # noqa: SLF001 - reuse the injected test factory
    return async_sessionmaker(request.app.state.async_engine, expire_on_commit=False)


def _principal_can_approve(principal: Any) -> bool:
    scopes = {str(s) for s in getattr(principal, "scopes", ()) or ()}
    roles = {str(r) for r in getattr(principal, "roles", ()) or ()}
    return bool(_APPROVE_SCOPES & scopes or _APPROVE_ROLES & roles)


@router.post("/runs/{run_id}/approvals/{approval_id}", status_code=200)
async def decide_approval(
    run_id: str,
    approval_id: str,
    body: ApprovalDecisionRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Approve or reject one approval for the caller's tenant.

    ``409`` on a digest/version conflict or an illegal transition, ``403`` when the
    caller lacks approval authority, ``404`` when the approval is unknown or belongs
    to another tenant (indistinguishable), ``200`` with the decided approval.
    """

    async def authority(tenant_id: str, user_id: str, action: str) -> str | None:
        # Fresh check against *this* request's principal: the service calls it at
        # decision time, so a revoked grant is seen immediately.
        if user_id != principal.user_id or tenant_id != principal.tenant_id:
            return "the decider must be the authenticated principal"
        if not _principal_can_approve(principal):
            return "the principal lacks approval authority"
        return None

    service = ApprovalService(
        factory=_session_factory(request), authority_check=authority
    )
    try:
        approval = await service.decide(
            tenant_id=principal.tenant_id,
            approval_id=approval_id,
            decision=body.decision,
            decided_by=principal.user_id,
            expected_digest=body.action_digest,
            expected_version=body.expected_version,
            reason=body.reason,
        )
    except ApprovalConflict as exc:
        raise ApplicationError(exc.code, str(exc), status_code=409) from exc
    except ApprovalStateError as exc:
        raise ApplicationError(exc.code, str(exc), status_code=409) from exc
    except ApprovalError as exc:
        status = 403 if exc.code == "AUTH_FORBIDDEN" else 404
        raise ApplicationError(exc.code, str(exc), status_code=status) from exc

    if approval.run_id != run_id:
        # The approval exists for this tenant but not under this run: a 404, not a
        # cross-run leak.
        raise ApplicationError(
            "RESOURCE_NOT_FOUND", "no such approval under this run", status_code=404
        )

    return {
        "approval_id": approval.id,
        "run_id": approval.run_id,
        "status": approval.status,
        "decided_by": approval.decided_by,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "version": approval.version,
    }


__all__ = ["router"]
