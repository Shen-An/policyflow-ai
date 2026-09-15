"""FastAPI dependencies for authentication, unit of work and authorization.

Two rules govern this module:

1. **Identity is membership-derived.** The tenant comes from the access token's
   tenant claim, the user from that tenant's own rows, and the membership from
   the grants tying the two together. A tenant or user id arriving in a body or
   query string is only ever compared against the membership - it can never
   *select* whose data is touched.
2. **Authorization is per request and fresh.** The principal carries a
   snapshot; anything with a side effect authorizes again through a grant source
   that re-reads the current grants, so a revocation lands immediately.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from backend.app.auth.authorization import (
    Action,
    AuthorizationService,
    Decision,
    ResourceRef,
)
from backend.app.auth.principal import (
    PrincipalRef,
    RequestPrincipal,
    RoleGrant,
    resolve_principal,
)
from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import decode_access_token
from backend.app.db.models import User
from backend.app.db.repositories import UnitOfWork, UnitOfWorkGrantSource

bearer_scheme = HTTPBearer(auto_error=False)

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Yield the synchronous session used by the pre-Stage-2 route surface."""
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db_session)]


def decode_request_token(
    request: Request,
    credentials: CredentialsDep,
) -> dict[str, Any]:
    """Return the validated access-token payload for this request.

    Raises:
        AuthenticationError: when the bearer credential is absent, malformed,
            expired, or not tenant scoped.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    return decode_access_token(credentials.credentials, request.app.state.settings)


TokenPayloadDep = Annotated[dict[str, Any], Depends(decode_request_token)]


def get_current_user(
    session: SessionDep,
    payload: TokenPayloadDep,
) -> User:
    """Return the authenticated user, scoped to the tenant named by the token.

    The lookup is deliberately tenant-qualified: a matching user id in another
    tenant is not this token's subject, so it is rejected rather than served.

    Raises:
        AuthenticationError: when the user is missing, belongs to a different
            tenant than the token, or is not active.
    """
    tenant_id = payload["tenant_id"]
    user = session.get(User, payload["sub"])
    if user is None:
        raise AuthenticationError("AUTH_INVALID_TOKEN", "Token user no longer exists")
    if user.tenant_id != tenant_id:
        raise AuthenticationError("AUTH_INVALID_TOKEN", "Token tenant does not own this user")
    if user.status != "active":
        raise AuthenticationError("AUTH_USER_DISABLED", "User account is disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_unit_of_work(request: Request) -> AsyncGenerator[UnitOfWork, None]:
    """Yield exactly one async unit of work for this request, always closing it.

    An override on ``app.state.uow_factory`` lets tests and alternative
    deployments supply their own session factory without patching this module.
    """
    factory = getattr(request.app.state, "uow_factory", None)
    # ``app.state.uow_factory`` builds a unit of work. It must not be handed to
    # ``UnitOfWork(factory=...)``, which expects an async session factory: doing
    # so gives every repository a unit of work where it expects a session, and
    # the mistake only appears when a route first touches one.
    uow = factory() if callable(factory) else UnitOfWork()
    try:
        yield uow
    finally:
        await uow.close()


UnitOfWorkDep = Annotated[UnitOfWork, Depends(get_unit_of_work)]


def _claimed_identity(request: Request, name: str) -> str | None:
    """Return a client-claimed tenant or user id, if the request carries one.

    Only scalar query-string values are read. A JSON body is intentionally not
    inspected here: a body field is validated by the route's own schema and then
    compared by :func:`resolve_principal`, which is the single place that decides
    whether a claim agrees with the membership.
    """
    value = request.query_params.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def get_principal(
    request: Request,
    uow: UnitOfWorkDep,
    payload: TokenPayloadDep,
) -> RequestPrincipal:
    """Derive the immutable principal for this request.

    Every read below is tenant-qualified, so no cross-tenant query is needed on
    the request path. A client-supplied ``tenant_id`` or ``user_id`` is passed to
    :func:`resolve_principal` purely so that any disagreement is rejected; the
    claimed value never wins.

    Raises:
        AuthenticationError: when the user or an active membership cannot be
            proven, or when a claimed identity contradicts the membership.
    """
    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]

    # Declare the tenant before the first read. Row-level security is forced on
    # the tenant-scoped tables, so a session that has not declared its tenant
    # reads nothing at all: without this, a valid member would look identical to
    # a missing one and every request would be refused for the wrong reason.
    await uow.set_tenant_context(tenant_id)

    tenant = await uow.tenants.get_visible(tenant_id)
    user = await uow.users.get(tenant_id, user_id)
    grants = await uow.grants.active_grants(tenant_id, user_id)
    if not grants:
        raise AuthenticationError(
            "AUTH_FORBIDDEN", "No active membership ties this user to the token's tenant"
        )

    membership = grants[0]
    role_codes: list[str] = []
    windows: list[RoleGrant] = []
    for grant in grants:
        role = await uow.roles.get(tenant_id, grant.role_id)
        role_codes.append(role.code)
        windows.append(
            RoleGrant(
                role_code=role.code,
                scopes=frozenset(getattr(role, "actions", ()) or ()),
                valid_from=grant.valid_from,
                expires_at=grant.expires_at,
            )
        )
    scopes = sorted({scope for window in windows for scope in window.scopes})
    source = UnitOfWorkGrantSource(uow)
    version = await source.authorization_version_for(tenant_id, user_id, membership.id)

    return resolve_principal(
        tenant,
        user,
        membership,
        role_codes=role_codes,
        scopes=scopes,
        grants=windows,
        authorization_version=version,
        session_id=payload.get("session_id", payload.get("jti", membership.id)),
        request_id=getattr(request.state, "request_id", membership.id),
        requested_tenant_id=_claimed_identity(request, "tenant_id"),
        requested_user_id=_claimed_identity(request, "user_id"),
    )


PrincipalDep = Annotated[RequestPrincipal, Depends(get_principal)]


async def get_authorization_service(uow: UnitOfWorkDep) -> AuthorizationService:
    """Return an authorization service backed by this request's grants.

    Grants are re-read from the unit of work, so ``authorize_fresh`` reflects a
    revocation made after the request began.
    """
    return AuthorizationService(
        grant_source=UnitOfWorkGrantSource(uow),
        resource_catalog=uow.resource_catalog,
    )


AuthorizationDep = Annotated[AuthorizationService, Depends(get_authorization_service)]


async def require_authorization(
    principal: PrincipalDep,
    service: AuthorizationDep,
    action: Action | str,
    resource_ref: ResourceRef,
) -> Decision:
    """Authorize an action freshly, raising on denial.

    Routes call this immediately before the effect they are about to perform.

    Raises:
        ApplicationError: the denial's own stable error code, always
            non-enumerable for resources that do not exist or are not visible.
    """
    decision = await service.authorize_fresh(principal, action, resource_ref)
    decision.raise_for_denial()
    return decision


def principal_ref_of(principal: RequestPrincipal) -> PrincipalRef:
    """Return the audit-safe reference for a principal."""
    return principal.principal_ref()
