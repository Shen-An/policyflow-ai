"""Identity, membership derivation and allow-only authorization primitives."""

from backend.app.auth.authorization import (
    Action,
    AuthorizationService,
    Decision,
    GrantSource,
    InMemoryGrantSource,
    ResourceRef,
    UnknownActionError,
    evaluate_authorization,
)
from backend.app.auth.error_codes import AUTH_FORBIDDEN, RESOURCE_NOT_FOUND, TENANT_NOT_FOUND
from backend.app.auth.principal import (
    PrincipalRef,
    RequestPrincipal,
    RoleGrant,
    resolve_principal,
)

__all__ = [
    "AUTH_FORBIDDEN",
    "RESOURCE_NOT_FOUND",
    "TENANT_NOT_FOUND",
    "Action",
    "AuthorizationService",
    "Decision",
    "GrantSource",
    "InMemoryGrantSource",
    "PrincipalRef",
    "RequestPrincipal",
    "ResourceRef",
    "RoleGrant",
    "UnknownActionError",
    "evaluate_authorization",
    "resolve_principal",
]
