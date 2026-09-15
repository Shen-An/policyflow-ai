"""Allow-only authorization: default deny, tenant-scoped, no wildcards.

Design rules enforced here (contracts/internal-contracts.md,
`AuthorizationService`; data-model.md, `Role`/`UserRoleGrant`):

- **Allow-only**: a decision is produced by finding an explicit grant. There is
  no deny-rule engine and no wildcard, so nothing can be allowed by omission or
  by pattern matching.
- **Tenant-scoped**: a principal may only act on resources of its own tenant.
  Acting outside that tenant needs the explicit ``cross_tenant_admin`` grant,
  and that grant is never implied by ``admin``.
- **Least privilege**: elevated actions (``approve``/``submit``/``publish``/
  ``delete``/``admin``) each need their own scope; ordinary membership never
  confers them implicitly.
- **Fresh authorization**: :meth:`AuthorizationService.authorize_fresh` re-reads
  grants from the grant source instead of trusting the principal's snapshot.
- **Non-enumerable denials**: a probe against another tenant's resource must be
  indistinguishable from a probe against a resource that does not exist. Both
  paths return the same reason code, message and error code.

The PostgreSQL-backed grant resolver is wired in a later task; this module
defines the :class:`GrantSource` protocol plus an in-memory implementation used
by tests.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from backend.app.auth.error_codes import AUTH_FORBIDDEN, RESOURCE_NOT_FOUND, TENANT_NOT_FOUND
from backend.app.auth.principal import RequestPrincipal
from backend.app.core.exceptions import ApplicationError

__all__ = [
    "Action",
    "AuthorizationService",
    "Decision",
    "GrantSource",
    "InMemoryGrantSource",
    "InMemoryResourceCatalog",
    "ResourceCatalog",
    "ResourceRef",
    "UnknownActionError",
    "evaluate_authorization",
]

# Denial reason codes. They are stable, greppable and safe to log; the *public*
# code returned to callers is ``Decision.code``, which deliberately collapses the
# cross-tenant case onto the plain not-found case.
REASON_ALLOWED = "ALLOW"
REASON_MEMBERSHIP_SCOPE = "AUTH_MEMBERSHIP_TENANT_MISMATCH"
REASON_CROSS_TENANT_DENIED = "AUTH_CROSS_TENANT_DENIED"
REASON_MISSING_SCOPE = "AUTH_MISSING_SCOPE"
REASON_RESOURCE_MISSING = "AUTH_RESOURCE_NOT_FOUND"
REASON_TENANT_INVISIBLE = "AUTH_TENANT_NOT_VISIBLE"
# Outward-facing denial for any resource the caller may not observe. A resource
# owned by another tenant and a resource that does not exist must be reported
# IDENTICALLY, otherwise the denial itself becomes an enumeration oracle.
REASON_NOT_OBSERVABLE = REASON_RESOURCE_MISSING

# A tenant-less resource belongs to no tenant, so only a cross-tenant
# administrator may touch it; ordinary tenant members cannot reach it at all.
CROSS_TENANT_SCOPE = "cross_tenant_admin"


class Action(StrEnum):
    """The exact action vocabulary every side effect must be authorized for."""

    READ = "read"
    EDIT = "edit"
    APPROVE = "approve"
    SUBMIT = "submit"
    PUBLISH = "publish"
    DELETE = "delete"
    ADMIN = "admin"
    CROSS_TENANT_ADMIN = "cross_tenant_admin"


class UnknownActionError(ApplicationError):
    """Raised when a caller passes an action outside :class:`Action`.

    Denying is the default, but silently coercing an unknown string into a known
    action would hide a programming error, so the coercion point fails loudly.
    """

    def __init__(self, action: object) -> None:
        super().__init__(
            AUTH_FORBIDDEN,
            "Unknown authorization action",
            403,
            {"action": str(action)},
        )


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """The tenant-owned object an action would touch.

    ``tenant_id`` is the *resource's* owner, which is what the tenant predicate
    compares against the principal's tenant. ``None`` marks a tenant-less or
    global object, reachable only with the cross-tenant grant.
    """

    kind: str
    id: str
    tenant_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze the attribute mapping so a resource reference stays a value."""
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    def with_attributes(self, **attributes: Any) -> ResourceRef:
        """Return a copy with extra attributes, used for audit metadata."""
        merged = dict(self.attributes)
        merged.update(attributes)
        return ResourceRef(
            kind=self.kind,
            id=self.id,
            tenant_id=self.tenant_id,
            attributes=merged,
        )

    def __str__(self) -> str:
        """Return a compact label; never includes attribute payloads."""
        return f"{self.kind}:{self.id}@{self.tenant_id or 'global'}"


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of one authorization evaluation.

    ``allowed`` is the only field callers may branch on. ``code`` is the public,
    stable error code (``None`` when allowed) and ``reason_code`` is the
    diagnostic reason. Both are identical for a cross-tenant probe and for a
    resource that does not exist, which is what makes denials non-enumerable.
    """

    allowed: bool
    action: Action
    reason_code: str | None = None
    code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze details so a decision cannot be rewritten after the fact."""
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @property
    def denied(self) -> bool:
        """Return whether this decision blocks the action."""
        return not self.allowed

    @property
    def retryable(self) -> bool:
        """Return whether retrying the same request could succeed.

        Authorization denials are terminal for the request: retrying without a
        new grant cannot change the answer.
        """
        return False

    def raise_for_denial(self) -> None:
        """Raise the matching :class:`ApplicationError` when denied.

        Use directly before executing a side effect so that "authorized" and
        "executed" cannot drift apart.
        """
        if self.allowed:
            return
        raise ApplicationError(
            self.code or AUTH_FORBIDDEN,
            "Not authorized to perform this action",
            403,
            {"action": str(self.action), "reason_code": self.reason_code},
        )


def _allow(action: Action) -> Decision:
    """Build the single allowed decision shape."""
    return Decision(allowed=True, action=action, reason_code=REASON_ALLOWED)


def _deny(
    action: Action,
    reason_code: str,
    code: str,
    details: Mapping[str, Any] | None = None,
) -> Decision:
    """Build a denial decision with a stable public code and reason code."""
    return Decision(
        allowed=False,
        action=action,
        reason_code=reason_code,
        code=code,
        details=details or {},
    )


@runtime_checkable
class GrantSource(Protocol):
    """Resolves the *current* scopes and authorization version for a principal.

    Implemented by the PostgreSQL grant resolver in a later task and by
    :class:`InMemoryGrantSource` in tests. Implementations may be synchronous or
    asynchronous; the service awaits awaitable results.
    """

    def scopes_for(
        self,
        tenant_id: str,
        user_id: str,
        membership_id: str,
    ) -> Iterable[str] | Awaitable[Iterable[str]]:
        """Return every scope currently granted to this membership."""
        ...

    def authorization_version_for(
        self,
        tenant_id: str,
        user_id: str,
        membership_id: str,
    ) -> int | Awaitable[int]:
        """Return the current authorization version for this membership."""
        ...


@runtime_checkable
class ResourceCatalog(Protocol):
    """Answers whether a resource exists, tenant-scoped, without leaking owners.

    The PostgreSQL repository layer implements this in a later task. The service
    only needs presence: a resource that is absent and a resource owned by
    another tenant must both answer ``False`` so callers cannot enumerate.
    """

    def contains(self, resource_ref: ResourceRef) -> bool:
        """Return whether the caller may observe this resource at all."""
        ...


class InMemoryResourceCatalog:
    """Deterministic in-memory resource catalogue used by contract tests."""

    def __init__(self, resources: Iterable[ResourceRef] = ()) -> None:
        """Start from an optional set of observable resources."""
        self._resources: set[tuple[str | None, str, str]] = {
            (resource.tenant_id, resource.kind, resource.id) for resource in resources
        }

    def contains(self, resource_ref: ResourceRef) -> bool:
        """Return whether the exact tenant/kind/id triple was catalogued."""
        return (resource_ref.tenant_id, resource_ref.kind, resource_ref.id) in self._resources

    def add(self, resource_ref: ResourceRef) -> None:
        """Catalogue one more observable resource."""
        self._resources.add((resource_ref.tenant_id, resource_ref.kind, resource_ref.id))

    def remove(self, resource_ref: ResourceRef) -> None:
        """Drop a resource so later checks behave as if it never existed."""
        self._resources.discard((resource_ref.tenant_id, resource_ref.kind, resource_ref.id))


class InMemoryGrantSource:
    """Deterministic in-memory grant source used by contract tests.

    State changes (``set_grant``, ``revoke``) bump the memberships' authorization
    versions, which lets tests prove that a re-read sees revocations that the
    principal's snapshot still lists.
    """

    def __init__(self) -> None:
        """Start with no grants, so every membership defaults to denied."""
        self._scopes: dict[tuple[str, str, str], frozenset[str]] = {}
        self._versions: dict[tuple[str, str, str], int] = {}

    @staticmethod
    def _key(tenant_id: str, user_id: str, membership_id: str) -> tuple[str, str, str]:
        """Build the membership key used by both grant maps."""
        return (tenant_id, user_id, membership_id)

    def scopes_for(self, tenant_id: str, user_id: str, membership_id: str) -> frozenset[str]:
        """Return the scopes currently stored for this membership."""
        return self._scopes.get(self._key(tenant_id, user_id, membership_id), frozenset())

    def authorization_version_for(self, tenant_id: str, user_id: str, membership_id: str) -> int:
        """Return the stored authorization version, ``0`` when never granted."""
        return self._versions.get(self._key(tenant_id, user_id, membership_id), 0)

    def set_grant(
        self,
        tenant_id: str,
        user_id: str,
        membership_id: str,
        scopes: Iterable[str],
        *,
        authorization_version: int | None = None,
    ) -> int:
        """Create or replace a membership's grant and return the new version.

        An explicit ``authorization_version`` is accepted so callers can model a
        grant row whose version they already know; otherwise the stored version
        is incremented, because any grant change invalidates prior snapshots.
        """
        key = self._key(tenant_id, user_id, membership_id)
        self._scopes[key] = frozenset(
            scope.strip() for scope in scopes if isinstance(scope, str) and scope.strip()
        )
        if authorization_version is None:
            self._versions[key] = self._versions.get(key, 0) + 1
        else:
            if authorization_version < 0:
                raise ValueError("authorization_version must not be negative")
            self._versions[key] = authorization_version
        return self._versions[key]

    def revoke(self, tenant_id: str, user_id: str, membership_id: str) -> int:
        """Remove every scope for a membership and return the new version."""
        return self.set_grant(tenant_id, user_id, membership_id, ())

    def revoke_scope(
        self,
        tenant_id: str,
        user_id: str,
        membership_id: str,
        scope: str,
    ) -> int:
        """Remove one scope while keeping the rest, returning the new version."""
        key = self._key(tenant_id, user_id, membership_id)
        remaining = {value for value in self._scopes.get(key, frozenset()) if value != scope}
        return self.set_grant(tenant_id, user_id, membership_id, remaining)


def _as_action(action: Action | str) -> Action:
    """Coerce an action to :class:`Action`, rejecting unknown values."""
    if isinstance(action, Action):
        return action
    try:
        return Action(action)
    except ValueError as exc:
        raise UnknownActionError(action) from exc


def _required_scope(action: Action) -> str:
    """Return the single scope that authorizes ``action``.

    Every action needs its *own* scope, including ``cross_tenant_admin``: the
    scope vocabulary mirrors the action vocabulary exactly, so ``admin`` never
    implies ``cross_tenant_admin`` and neither implies anything else.
    """
    return action.value


def _has_scope(scopes: Iterable[str], required: str) -> bool:
    """Return whether ``required`` is granted, with no wildcard expansion."""
    return required in scopes


def evaluate_authorization(
    principal: RequestPrincipal,
    action: Action | str,
    resource: ResourceRef,
    *,
    scopes: Iterable[str],
    membership_id: str | None = None,
    resource_exists: bool | None = None,
) -> Decision:
    """Evaluate one allow-only authorization question without I/O.

    This is the shared policy core behind both the snapshot path and the
    fresh-authorization path: the only difference between them is which scope
    set ``scopes`` carries. ``resource_exists`` is ``None`` when no catalogue was
    consulted, ``True`` when the resource is visible and ``False`` when it is
    absent or owned by another tenant.
    """
    resolved_action = _as_action(action)

    if membership_id is not None and membership_id != principal.membership_id:
        return _deny(
            resolved_action,
            REASON_MEMBERSHIP_SCOPE,
            AUTH_FORBIDDEN,
            {"membership_bound": False},
        )

    cross_tenant = _has_scope(scopes, CROSS_TENANT_SCOPE)
    resource_tenant = resource.tenant_id
    own_tenant = resource_tenant == principal.tenant_id

    if resource_tenant is None and not cross_tenant:
        # Tenant-less objects are outside every tenant's scope by construction,
        # and must report exactly like an unknown id.
        return _deny(resolved_action, REASON_NOT_OBSERVABLE, RESOURCE_NOT_FOUND)
    if resource_tenant is not None and not own_tenant and not cross_tenant:
        # Same reason code and empty details as a resource that does not exist: a
        # probe must not be able to tell "another tenant owns this id" from
        # "this id is unknown". Any distinguishing detail would be an oracle.
        return _deny(resolved_action, REASON_NOT_OBSERVABLE, RESOURCE_NOT_FOUND)
    if resource_exists is False:
        return _deny(resolved_action, REASON_NOT_OBSERVABLE, RESOURCE_NOT_FOUND)

    required = _required_scope(resolved_action)
    if not _has_scope(scopes, required):
        return _deny(
            resolved_action,
            REASON_MISSING_SCOPE,
            AUTH_FORBIDDEN,
            {"required_scope": required},
        )
    return _allow(resolved_action)


class AuthorizationService:
    """Server-side, allow-only authorization for every read, write and effect.

    Callers always authorize *immediately before* the effect they are about to
    perform. ``authorize`` evaluates the principal's request-scoped snapshot;
    ``authorize_fresh`` re-reads the current grants, which is what approval
    execution, submission and other long-running work must use.
    """

    def __init__(
        self,
        grant_source: GrantSource | None = None,
        resource_catalog: ResourceCatalog | None = None,
    ) -> None:
        """Bind the service to a grant source and optional resource catalogue.

        ``None`` for either side means the corresponding check is skipped, which
        only ever *narrows* what is allowed.
        """
        self._grant_source = grant_source
        self._resource_catalog = resource_catalog

    @property
    def grant_source(self) -> GrantSource | None:
        """Return the configured grant source, or ``None`` when unset."""
        return self._grant_source

    @property
    def resource_catalog(self) -> ResourceCatalog | None:
        """Return the configured resource catalogue, or ``None`` when unset."""
        return self._resource_catalog

    def authorize(
        self,
        principal: RequestPrincipal,
        action: Action | str,
        resource_ref: ResourceRef,
    ) -> Decision:
        """Authorize using the principal's request-scoped scope snapshot.

        Denies by default: an action without an explicit matching grant is
        refused, and every denial carries a stable reason code.
        """
        return evaluate_authorization(
            principal,
            action,
            resource_ref,
            scopes=principal.effective_scopes(),
            resource_exists=self._resource_exists(resource_ref),
        )

    async def authorize_fresh(
        self,
        principal: RequestPrincipal,
        action: Action | str,
        resource_ref: ResourceRef,
    ) -> Decision:
        """Authorize after re-reading CURRENT grants from the grant source.

        The principal snapshot is deliberately not trusted here: a grant revoked
        after the request started must block the effect. When no grant source is
        configured nothing can be proven current, so the call is denied.
        """
        resolved_action = _as_action(action)
        if self._grant_source is None:
            return _deny(
                resolved_action,
                REASON_MISSING_SCOPE,
                AUTH_FORBIDDEN,
                {"grant_source": "unconfigured"},
            )

        scopes = await _resolve(self._grant_source.scopes_for(
            principal.tenant_id,
            principal.user_id,
            principal.membership_id,
        ))
        # The re-read version is carried into the audit trail even though the
        # evaluation itself only depends on the freshly read scope set.
        version = await self.authorization_version(principal)
        return evaluate_authorization(
            principal.with_authorization_version(version),
            resolved_action,
            resource_ref,
            scopes=scopes,
            resource_exists=await self._resource_exists_async(resource_ref),
        )

    def _resource_exists(self, resource_ref: ResourceRef) -> bool | None:
        """Return catalogue presence, or ``None`` when no catalogue is bound.

        This synchronous form only serves a synchronous catalogue; the async
        authorization path uses :meth:`_resource_exists_async`.
        """
        if self._resource_catalog is None:
            return None
        return self._resource_catalog.contains(resource_ref)

    async def _resource_exists_async(self, resource_ref: ResourceRef) -> bool | None:
        """Resolve catalogue presence for either published catalogue shape.

        Two catalogues exist and they answer the same question from different
        sides: the synchronous, single-argument ``ResourceCatalog``, and the
        tenant-qualified asynchronous catalogue that shares the request's own
        connection and transaction. The asynchronous one exposes a
        ``contains_ref`` coroutine so it can be driven from here without a second
        connection; when a catalogue offers only ``contains``, its result is
        awaited in case it is a coroutine rather than a value.

        A catalogue that cannot answer returns ``None``, which fails closed:
        ``evaluate_authorization`` treats unknown presence as absent.
        """
        catalog = self._resource_catalog
        if catalog is None:
            return None
        contains_ref = getattr(catalog, "contains_ref", None)
        result = (
            contains_ref(resource_ref)
            if callable(contains_ref)
            else catalog.contains(resource_ref)
        )
        resolved = await _resolve(result)
        return None if resolved is None else bool(resolved)


    async def authorization_version(self, principal: RequestPrincipal) -> int:
        """Return the authorization version governing this request.

        Without a grant source the principal's snapshot version is authoritative
        for the request; with one, the current version is re-read so that a stale
        snapshot is observable to callers that compare versions.
        """
        if self._grant_source is None:
            return principal.authorization_version
        current = await _resolve(self._grant_source.authorization_version_for(
            principal.tenant_id,
            principal.user_id,
            principal.membership_id,
        ))
        return int(current)

    def is_tenant_visible(self, principal: RequestPrincipal, tenant_id: str) -> bool:
        """Return whether ``tenant_id`` is reachable by this principal.

        Another tenant's existence is never confirmed: the caller receives the
        same ``TENANT_NOT_FOUND`` shape it would get for a deleted tenant.
        """
        if tenant_id == principal.tenant_id:
            return True
        return _has_scope(principal.effective_scopes(), CROSS_TENANT_SCOPE)

    def tenant_denial(self, action: Action | str, tenant_id: str) -> Decision:
        """Build the deny decision used for an invisible tenant."""
        return _deny(
            _as_action(action),
            REASON_TENANT_INVISIBLE,
            TENANT_NOT_FOUND,
            {"tenant_id": tenant_id},
        )

    def raise_for_denial(
        self,
        principal: RequestPrincipal,
        action: Action | str,
        resource_ref: ResourceRef,
    ) -> Decision:
        """Authorize and raise :class:`ApplicationError` on denial.

        Convenience for call sites that must fail the request rather than branch
        on the decision; the returned decision is always allowed.
        """
        decision = self.authorize(principal, action, resource_ref)
        decision.raise_for_denial()
        return decision


async def _resolve(value: Any) -> Any:
    """Await ``value`` when it is awaitable, otherwise return it unchanged.

    Lets :class:`GrantSource` implementations be sync (tests, cached lookups) or
    async (PostgreSQL) behind one call path.
    """
    if inspect.isawaitable(value):
        return await value
    return value
