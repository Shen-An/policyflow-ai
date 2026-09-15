"""Version 2 routes that run entirely on the tenant-scoped request context.

Nothing here accepts a tenant or a user from the request body or the query
string: both come from the signed token and are re-checked against stored
membership. The surface is deliberately small - it is the first place where the
tenant-scoped request context is exercised inside the assembled application
rather than only under test.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.app.api.deps import PrincipalDep

router = APIRouter(prefix="/api/v2", tags=["v2"])


@router.get("/principal")
async def read_principal(principal: PrincipalDep) -> dict[str, Any]:
    """Return the caller's own principal, derived from stored membership.

    A caller can only ever read its own identity: the tenant and user in the
    response come from the membership named by the token, never from the request.
    """
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "membership_id": principal.membership_id,
        "roles": sorted(principal.roles),
        "scopes": sorted(principal.scopes),
        "authorization_version": principal.authorization_version,
    }
