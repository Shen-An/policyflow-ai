"""Authentication service functions."""

from sqlmodel import Session, select

from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import verify_password
from backend.app.db.models import Tenant, User
from backend.app.services.user_service import get_user_by_username_in_tenant

ACTIVE_STATUS = "active"


def resolve_login_tenant(session: Session, tenant_code: str | None = None) -> Tenant:
    """Resolve which tenant a login attempt is made against.

    A login must name its tenant whenever more than one could match, because
    usernames are unique *per tenant* and not globally: ``admin`` in two tenants
    is two different people. When no code is supplied the single active tenant is
    used, which keeps single-tenant deployments (including the development
    default) working without a tenant parameter.

    Raises:
        AuthenticationError: when no active tenant matches. A wrong or inactive
            tenant code is reported as bad credentials rather than as a missing
            tenant, so the endpoint cannot be used to enumerate tenant codes.
        AuthenticationError: code ``AUTH_TENANT_REQUIRED`` when several tenants
            are active and none was named. This reveals only that the deployment
            is multi-tenant, never which tenants exist.
    """
    if tenant_code is not None and tenant_code.strip():
        tenant = session.exec(
            select(Tenant).where(Tenant.code == tenant_code.strip())
        ).first()
        if tenant is None or tenant.status != ACTIVE_STATUS:
            raise AuthenticationError("AUTH_INVALID_CREDENTIALS", "Invalid username or password")
        return tenant

    candidates = session.exec(select(Tenant).where(Tenant.status == ACTIVE_STATUS)).all()
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise AuthenticationError("AUTH_INVALID_CREDENTIALS", "Invalid username or password")
    raise AuthenticationError(
        "AUTH_TENANT_REQUIRED",
        "This deployment serves several tenants; name a tenant code to log in",
    )


def authenticate_user(
    session: Session,
    username: str,
    password: str,
    tenant_code: str | None = None,
) -> tuple[User, Tenant]:
    """Authenticate within a resolved tenant and return the user with its tenant.

    The tenant is resolved *before* the user lookup, so a username can only ever
    match inside the tenant the caller asked for. Credential failures and
    cross-tenant username misses are indistinguishable to the caller.
    """
    tenant = resolve_login_tenant(session, tenant_code)
    user = get_user_by_username_in_tenant(session, tenant.id, username)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthenticationError("AUTH_INVALID_CREDENTIALS", "Invalid username or password")
    if user.status != ACTIVE_STATUS:
        raise AuthenticationError("AUTH_USER_DISABLED", "User account is disabled")
    return user, tenant
