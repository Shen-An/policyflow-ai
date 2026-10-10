"""T143 [US5] ``/api/v2/admin``: tenant governance over HTTP.

The surface is deliberately narrow and declarative:

* every route takes its tenant from the bearer principal -- there is no
  ``tenant_id`` body/query field to smuggle;
* every route delegates to :class:`AdminService`, which authorizes freshly and
  audits the decision, so the HTTP layer contains no second authorization rule;
* pagination is explicit (``limit``/``offset``) and the effective window is
  echoed back, so a dashboard never silently renders a partial answer;
* denials and missing ids share the same non-enumerable ``404``/``403`` shapes
  the rest of the v2 surface uses.

Platform capacity evidence is the one cross-tenant read, and it needs the
separate ``cross_tenant_admin`` scope: a tenant admin gets a plain 403 rather
than a redacted list that would still confirm other tenants' load tests exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.api.deps import PrincipalDep
from backend.app.services.admin_service import (
    PAGE_LIMIT_DEFAULT,
    PAGE_LIMIT_MAX,
    AdminService,
)

router = APIRouter(prefix="/api/v2/admin", tags=["v2", "admin"])


def _admin_service(request: Request) -> AdminService:
    """Return the admin service for this app, honouring a test override.

    The default reads the app's async engine, so the route surface is exercised
    against the same unit-of-work factory the request path uses. Tests may inject
    ``app.state.admin_service`` (for example to point at a scratch database).
    """
    override = getattr(request.app.state, "admin_service", None)
    if override is not None:
        return override
    cached = getattr(request.app.state, "_default_admin_service", None)
    if cached is not None:
        return cached
    factory = async_sessionmaker(request.app.state.async_engine, expire_on_commit=False)
    service = AdminService(factory=factory)
    request.app.state._default_admin_service = service
    return service


class GrantCreateRequest(BaseModel):
    """Body for creating a role grant. Tenant/user identity is not accepted."""

    user_id: str = Field(min_length=1, max_length=36)
    role_id: str | None = Field(default=None, min_length=1, max_length=36)
    role_code: str | None = Field(default=None, min_length=1, max_length=50)
    valid_from: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _exactly_one_role_reference(self) -> GrantCreateRequest:
        if bool(self.role_id) == bool(self.role_code):
            raise ValueError("provide exactly one of role_id or role_code")
        if self.valid_from and self.expires_at and self.expires_at <= self.valid_from:
            raise ValueError("expires_at must be later than valid_from")
        return self


class GrantRevokeRequest(BaseModel):
    """Body for revoking a grant; the reason is optional and audited."""

    reason: str | None = Field(default=None, max_length=500)


class RunRecoveryRequest(BaseModel):
    """Body for putting a recoverable-failed run back on the queue."""

    extend_attempts: bool = False


class ReconciliationRetryRequest(BaseModel):
    """Body for retrying a human-escalated reconciliation finding."""

    reason: str | None = Field(default=None, max_length=500)


class ReconciliationResolveRequest(BaseModel):
    """Body for closing a finding with a resolution note."""

    resolution: str = Field(min_length=1, max_length=500)


@router.get("/roles")
async def list_roles(principal: PrincipalDep, request: Request) -> Any:
    """List this tenant's roles, their actions and active grant counts."""
    return await _admin_service(request).list_roles(principal)


@router.get("/grants")
async def list_grants(
    principal: PrincipalDep,
    request: Request,
    limit: int = Query(PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    member_id: str | None = Query(default=None, max_length=36),
) -> Any:
    """List grants in this tenant, optionally filtered to one member.

    ``member_id`` is a filter, not an identity claim: the platform reserves
    ``user_id``/``tenant_id`` in a request for claims that must match the token.
    """
    return await _admin_service(request).list_grants(
        principal, limit=limit, offset=offset, member_id=member_id
    )


@router.post("/grants", status_code=201)
async def create_grant(
    body: GrantCreateRequest, principal: PrincipalDep, request: Request
) -> Any:
    """Grant one role to one member of this tenant.

    ``404`` for a role or member outside the caller's tenant (no enumeration),
    ``403`` when the grant would exceed the operator's own scopes, ``201`` on
    creation, ``200``-shaped body with ``created: false`` for an idempotent
    repeat.
    """
    return await _admin_service(request).create_grant(
        principal,
        user_id=body.user_id,
        role_id=body.role_id,
        role_code=body.role_code,
        valid_from=body.valid_from,
        expires_at=body.expires_at,
    )


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(
    grant_id: str,
    body: GrantRevokeRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Revoke a grant; a repeat is an idempotent no-op."""
    return await _admin_service(request).revoke_grant(
        principal, grant_id=grant_id, reason=body.reason
    )


@router.get("/quota")
async def quota_summary(principal: PrincipalDep, request: Request) -> Any:
    """Return effective quota policies, usage totals and live lease count."""
    return await _admin_service(request).quota_summary(principal)


@router.get("/health")
async def health_summary(principal: PrincipalDep, request: Request) -> Any:
    """Return tenant-scoped queue, indexing, submission and drift health."""
    return await _admin_service(request).health(principal)


@router.get("/runs")
async def list_runs(
    principal: PrincipalDep,
    request: Request,
    limit: int = Query(PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    status: str | None = Query(default=None, max_length=30),
    kind: str | None = Query(default=None, max_length=30),
) -> Any:
    """List this tenant's runs, newest first."""
    return await _admin_service(request).list_runs(
        principal, limit=limit, offset=offset, status=status, kind=kind
    )


@router.get("/runs/{run_id}/audit")
async def run_audit(
    run_id: str,
    principal: PrincipalDep,
    request: Request,
    limit: int = Query(200, ge=1, le=500),
) -> Any:
    """Return one run's correlated, redacted audit timeline."""
    return await _admin_service(request).run_audit(
        principal, run_id=run_id, limit=limit
    )


@router.post("/runs/{run_id}/recover")
async def recover_run(
    run_id: str,
    body: RunRecoveryRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Requeue a ``recoverable_failed`` run; terminal runs are a 409."""
    return await _admin_service(request).recover_run(
        principal, run_id=run_id, extend_attempts=body.extend_attempts
    )


@router.get("/reconciliation")
async def list_reconciliation(
    principal: PrincipalDep,
    request: Request,
    limit: int = Query(PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    state: str | None = Query(default=None, max_length=20),
    issue_kind: str | None = Query(default=None, max_length=30),
) -> Any:
    """List cross-store findings (fingerprints only, never storage keys)."""
    return await _admin_service(request).list_reconciliation(
        principal,
        limit=limit,
        offset=offset,
        state=state,
        issue_kind=issue_kind,
    )


@router.post("/reconciliation/{issue_id}/retry")
async def retry_reconciliation(
    issue_id: str,
    body: ReconciliationRetryRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Reopen a finding with a fresh attempt budget."""
    return await _admin_service(request).retry_reconciliation(
        principal, issue_id=issue_id
    )


@router.post("/reconciliation/{issue_id}/resolve")
async def resolve_reconciliation(
    issue_id: str,
    body: ReconciliationResolveRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Close a finding with an operator-supplied resolution note."""
    return await _admin_service(request).resolve_reconciliation(
        principal, issue_id=issue_id, resolution=body.resolution
    )


@router.get("/capacity")
async def list_capacity(
    principal: PrincipalDep,
    request: Request,
    limit: int = Query(PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
) -> Any:
    """List platform capacity runs; requires ``cross_tenant_admin``."""
    return await _admin_service(request).list_capacity(
        principal, limit=limit, offset=offset
    )


__all__ = ["router"]
