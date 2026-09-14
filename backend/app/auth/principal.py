"""Immutable request principal derived from a validated membership record.

This module owns *identity*, not *permission* decisions: it answers "who is
calling, for which tenant, under which authorization version" and refuses to
build a principal whose claimed tenant/user disagrees with the authenticated
membership. Permission questions are answered by
:mod:`backend.app.auth.authorization`.

Identity invariants (contracts/internal-contracts.md, `RequestPrincipal`):

- Tenant identity comes only from a validated membership row. A request body,
  query string or header may *name* a tenant/user, but it can never *establish*
  one; a disagreement is a rejection, never a silent override.
- The object is immutable for one request/worker attempt. Elevated work such as
  approval execution must build a fresh principal instead of mutating this one.
- Only reference identifiers may be propagated into graph state and audit
  context; credentials, tokens and personal data beyond ids never live here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from backend.app.auth.error_codes import AUTH_FORBIDDEN
from backend.app.core.exceptions import ApplicationError

__all__ = [
    "ActiveStatusEntity",
    "PrincipalMembership",
    "PrincipalRef",
    "PrincipalUser",
    "RequestPrincipal",
    "RoleGrant",
    "principal_error",
    "resolve_principal",
]

# Statuses that permit identity use. Any other value fails closed, so an
# unknown future status cannot silently become an authorization bypass.
_ACTIVE_STATUS = "active"
# Disabled/revoked statuses that must never yield a usable principal.
_INACTIVE_STATUSES = frozenset({"suspended", "disabled", "deleted", "revoked", "expired"})


def principal_error(message: str, details: Any | None = None) -> ApplicationError:
    """Build the project-standard rejection error for identity failures.

    Always the same ``AUTH_FORBIDDEN`` code, message shape and status: a caller
    probing another tenant's membership must not be able to distinguish that
    probe from a plain bad request.
    """
    return ApplicationError(AUTH_FORBIDDEN, message, 403, details)


@runtime_checkable
class ActiveStatusEntity(Protocol):
    """Anything exposing a lifecycle ``status`` (Tenant and User both do)."""

    status: str


@runtime_checkable
class PrincipalUser(Protocol):
    """The authenticated user record: a stable id plus a lifecycle status."""

    id: UUID | str
    status: str


@runtime_checkable
class PrincipalMembership(Protocol):
    """The authoritative link between one user and the tenant they act in.

    ``tenant_id``/``user_id`` on this record are the *only* accepted source of
    tenant identity for a principal.
    """

    id: UUID | str
    tenant_id: UUID | str
    user_id: UUID | str
    status: str


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """One role assignment with its validity window and revocation marker.

    Mirrors the `UserRoleGrant` row: a grant only contributes roles/scopes
    while it is non-revoked and inside ``[valid_from, expires_at)``. Time
    comparison is only performed when the caller supplies ``now``, which keeps
    derivation deterministic for callers that pass pre-validated grants.
    """

    role_code: str
    scopes: frozenset[str] = frozenset()
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    revoked: bool = False

    def is_valid_at(self, now: datetime | None) -> bool:
        """Return whether this grant is currently usable.

        A revoked grant is never usable. When ``now`` is given, the grant must
        also fall inside its validity window; when it is omitted only the
        revocation flag and the window's internal consistency are enforced.
        """
        if self.revoked:
            return False
        if self.valid_from is not None and self.expires_at is not None:
            if self.expires_at <= self.valid_from:
                return False
        if now is None:
            return True
        if self.valid_from is not None and now < _as_utc(self.valid_from):
            return False
        return not (self.expires_at is not None and now >= _as_utc(self.expires_at))


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    """The non-secret reference to a principal, safe for graph state and audit.

    Contains only tenant/user/membership identifiers and the authorization
    version; no scopes, roles, session identifiers, tokens or personal data.
    """

    tenant_id: str
    user_id: str
    membership_id: str
    authorization_version: int

    def as_context(self) -> dict[str, str | int]:
        """Return the reference as a JSON-ready mapping for graph/audit state."""
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "membership_id": self.membership_id,
            "authorization_version": self.authorization_version,
        }

    def __str__(self) -> str:
        """Return a compact stable label used in logs and audit records."""
        return (
            f"tenant={self.tenant_id} user={self.user_id} "
            f"membership={self.membership_id} authz_v={self.authorization_version}"
        )


@dataclass(frozen=True, slots=True)
class RequestPrincipal:
    """Immutable caller identity for one request or worker attempt.

    Constructed exclusively through :func:`resolve_principal` so that tenant
    identity is always membership-derived; direct construction is reserved for
    tests and for rehydrating a :class:`PrincipalRef` inside trusted code.
    """

    tenant_id: str
    user_id: str
    membership_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    authorization_version: int
    session_id: str
    request_id: str
    run_id: str | None = None

    def effective_scopes(self) -> frozenset[str]:
        """Return the principal's effective scope set.

        This is a derived value, not a mutable view: callers receive the frozen
        snapshot and cannot widen their own permissions in place.
        """
        return frozenset(self.scopes)

    def effective_roles(self) -> frozenset[str]:
        """Return the principal's role codes as a set."""
        return frozenset(self.roles)

    def principal_ref(self) -> PrincipalRef:
        """Return the non-secret reference used for graph state and audit."""
        return PrincipalRef(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            membership_id=self.membership_id,
            authorization_version=self.authorization_version,
        )

    def with_run(self, run_id: str) -> RequestPrincipal:
        """Return a copy of this principal bound to ``run_id``.

        One principal legitimately drives several runs (chat, eval, workflow),
        so binding a run returns a new immutable value instead of mutating the
        shared instance.
        """
        return replace(self, run_id=_require_identifier(run_id, "run_id"))

    def with_authorization_version(self, authorization_version: int) -> RequestPrincipal:
        """Return a copy carrying a freshly read authorization version.

        Used when a long-running or resumed operation re-reads current grants;
        the original request-scoped value stays untouched.
        """
        if not isinstance(authorization_version, int) or isinstance(authorization_version, bool):
            raise principal_error("authorization_version must be an integer")
        if authorization_version < 0:
            raise principal_error("authorization_version must not be negative")
        return replace(self, authorization_version=authorization_version)


def _as_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime without changing the instant."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stringify_identifier(value: object) -> str | None:
    """Return a stable string form for a UUID/str identifier, else ``None``."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    return None


def _attribute(entity: object, name: str) -> object | None:
    """Read ``name`` from an entity-like object, tolerating mappings."""
    if isinstance(entity, Mapping):
        return entity.get(name)
    return getattr(entity, name, None)


def _require_identifier(value: object, field_name: str) -> str:
    """Return ``value`` as a non-empty identifier or raise a forbidden error."""
    identifier = _stringify_identifier(value)
    if identifier is None:
        raise principal_error(f"{field_name} must be a UUID or non-empty string")
    return identifier


def _require_entity_identifier(entity: object | None, field_name: str) -> str:
    """Return the id of a required entity such as Tenant or User."""
    if entity is None:
        raise principal_error(f"{field_name} is required to authenticate this request")
    return _require_identifier(_attribute(entity, "id"), f"{field_name}.id")


def _ensure_active(entity: object | None, field_name: str, expected_status: str) -> None:
    """Fail closed when a tenant/user/membership is missing or not usable.

    An entity without a ``status`` attribute is treated as active, which keeps
    derivation usable for minimal test doubles while still rejecting any status
    the caller can actually observe.
    """
    if not isinstance(expected_status, str) or not expected_status.strip():
        raise principal_error("expected_status must be a non-empty string")
    if expected_status.strip().casefold() in _INACTIVE_STATUSES:
        raise principal_error("expected_status must be an active status")
    if entity is None:
        return
    status = _attribute(entity, "status")
    if status is None:
        return
    if not isinstance(status, str):
        raise principal_error(f"{field_name}.status must be a string")
    normalized = status.strip().casefold()
    if normalized == expected_status.strip().casefold():
        return
    if normalized in _INACTIVE_STATUSES:
        raise principal_error(f"{field_name} is not active")
    raise principal_error(f"{field_name} has an unrecognized status")


def _unique_codes(values: Iterable[str], field_name: str) -> frozenset[str]:
    """Return a validated, deduplicated set of role/scope codes."""
    if isinstance(values, str):
        raise principal_error(f"{field_name} must be a collection of codes, not a string")
    collected: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise principal_error(f"{field_name} entries must be non-empty strings")
        collected.add(value.strip())
    return frozenset(collected)


def resolve_principal(
    tenant: object,
    user: object,
    membership: object,
    *,
    role_codes: Iterable[str] = (),
    scopes: Iterable[str] = (),
    grants: Iterable[RoleGrant] = (),
    authorization_version: int = 0,
    session_id: object,
    request_id: object,
    run_id: object | None = None,
    requested_tenant_id: object | None = None,
    requested_user_id: object | None = None,
    now: datetime | None = None,
    expected_status: str = _ACTIVE_STATUS,
) -> RequestPrincipal:
    """Derive the immutable principal for one request from validated membership.

    ``tenant``/``user``/``membership`` are the authenticated records resolved by
    the API dependency layer; ``requested_tenant_id``/``requested_user_id`` are
    whatever a body, query string or header *claimed*. The claimed values are
    only ever compared, never trusted: any disagreement with the membership
    (and any inactive tenant/user/membership) raises ``AUTH_FORBIDDEN``.

    ``grant_scopes``/``role_codes`` are the values the membership rows imply;
    ``grants`` carries the per-grant validity windows and revocation markers, so
    expired or revoked grants are dropped even when their code is still listed.

    Raises:
        ApplicationError: code ``AUTH_FORBIDDEN`` when identity cannot be
            established from the membership record.
    """
    if membership is None:
        raise principal_error("Validated membership is required to build a principal")

    membership_id = _require_identifier(_attribute(membership, "id"), "membership.id")
    membership_tenant_id = _require_identifier(
        _attribute(membership, "tenant_id"), "membership.tenant_id"
    )
    membership_user_id = _require_identifier(_attribute(membership, "user_id"), "membership.user_id")

    tenant_id = _require_entity_identifier(tenant, "tenant")
    user_id = _require_entity_identifier(user, "user")

    if membership_tenant_id != tenant_id:
        raise principal_error("Membership does not belong to the authenticated tenant")
    if membership_user_id != user_id:
        raise principal_error("Membership does not belong to the authenticated user")

    for field_name, claimed in (
        ("tenant_id", requested_tenant_id),
        ("user_id", requested_user_id),
    ):
        if claimed is None:
            continue
        claimed_id = _stringify_identifier(claimed)
        if claimed_id is None:
            raise principal_error(f"Request-supplied {field_name} must be a UUID or string")
        expected = tenant_id if field_name == "tenant_id" else user_id
        if claimed_id != expected:
            raise principal_error(
                f"Request-supplied {field_name} does not match the authenticated membership"
            )

    _ensure_active(tenant, "tenant", expected_status)
    _ensure_active(user, "user", expected_status)
    _ensure_active(membership, "membership", expected_status)

    if not isinstance(authorization_version, int) or isinstance(authorization_version, bool):
        raise principal_error("authorization_version must be an integer")
    if authorization_version < 0:
        raise principal_error("authorization_version must not be negative")

    valid_grants = tuple(grant for grant in grants if grant.is_valid_at(now))
    declared_roles = _unique_codes(role_codes, "role_codes")
    declared_scopes = _unique_codes(scopes, "scopes")

    # A grant whose window has closed (or that was revoked) can no longer
    # contribute its role or its scopes, even if a stale list still names it.
    granted_roles = frozenset(grant.role_code for grant in valid_grants)
    granted_scopes = frozenset(scope for grant in valid_grants for scope in grant.scopes)
    roles = declared_roles & granted_roles if declared_roles else granted_roles
    scope_set = declared_scopes & granted_scopes if declared_scopes else granted_scopes

    return RequestPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        roles=roles,
        scopes=scope_set,
        authorization_version=authorization_version,
        session_id=_require_identifier(session_id, "session_id"),
        request_id=_require_identifier(request_id, "request_id"),
        run_id=None if run_id is None else _require_identifier(run_id, "run_id"),
    )
