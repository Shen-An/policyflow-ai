"""Contract tests for membership-derived principals and allow-only RBAC (T020).

These tests are intentionally database-free: they lock the *semantics* of
``backend/app/auth/principal.py`` and ``backend/app/auth/authorization.py``
before the PostgreSQL-backed repositories land. Assertions here are written as
failures for the pre-implementation state (no principal, no authorization
decision object), so they double as the T020 failure tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.auth.authorization import (
    Action,
    AuthorizationService,
    InMemoryGrantSource,
    InMemoryResourceCatalog,
    ResourceRef,
    UnknownActionError,
)
from backend.app.auth.principal import RequestPrincipal, RoleGrant, resolve_principal
from backend.app.core.exceptions import ApplicationError

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
USER_A = "33333333-3333-4333-8333-333333333333"
USER_B = "44444444-4444-4444-8444-444444444444"
MEMBERSHIP_A = "55555555-5555-4555-8555-555555555555"
MEMBERSHIP_B = "66666666-6666-4666-8666-666666666666"
SESSION_ID = "77777777-7777-4777-8777-777777777777"
REQUEST_ID = "req-contract-0001"
RUN_ID = "88888888-8888-4888-8888-888888888888"


@dataclass(frozen=True)
class StubEntity:
    """Minimal stand-in for the Tenant/User/membership rows resolved by the API."""

    id: str
    status: str = "active"


def make_principal(
    *,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    membership_id: str = MEMBERSHIP_A,
    scopes: frozenset[str] = frozenset(),
    roles: frozenset[str] = frozenset(),
    authorization_version: int = 1,
    run_id: str | None = None,
) -> RequestPrincipal:
    """Build a principal directly, for tests that do not exercise derivation."""
    return RequestPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        roles=roles,
        scopes=scopes,
        authorization_version=authorization_version,
        session_id=SESSION_ID,
        request_id=REQUEST_ID,
        run_id=run_id,
    )


@pytest.fixture
def membership_inputs() -> dict[str, object]:
    """The authenticated records every resolution test starts from."""
    return {
        "tenant": StubEntity(id=TENANT_A),
        "user": StubEntity(id=USER_A),
        "membership": {
            "id": MEMBERSHIP_A,
            "tenant_id": TENANT_A,
            "user_id": USER_A,
            "status": "active",
        },
        "session_id": SESSION_ID,
        "request_id": REQUEST_ID,
    }


@pytest.fixture
def grant_source() -> InMemoryGrantSource:
    """A grant source holding one membership whose grants tests mutate."""
    source = InMemoryGrantSource()
    source.set_grant(TENANT_A, USER_A, MEMBERSHIP_A, {Action.READ.value})
    return source


# --------------------------------------------------------------------------- #
# Deliverable 1: RequestPrincipal (T024)
# --------------------------------------------------------------------------- #


def test_principal_is_derived_from_membership(membership_inputs: dict[str, object]) -> None:
    """Identity comes from the validated membership, not from caller input."""
    principal = resolve_principal(
        **membership_inputs,  # type: ignore[arg-type]
        role_codes=["employee"],
        scopes=["read"],
        authorization_version=7,
    )

    assert principal.tenant_id == TENANT_A
    assert principal.user_id == USER_A
    assert principal.membership_id == MEMBERSHIP_A
    assert principal.authorization_version == 7
    assert principal.session_id == SESSION_ID
    assert principal.request_id == REQUEST_ID
    assert principal.run_id is None


def test_body_supplied_tenant_is_rejected(membership_inputs: dict[str, object]) -> None:
    """A body/query tenant that disagrees with the membership is refused."""
    with pytest.raises(ApplicationError) as excinfo:
        resolve_principal(
            **membership_inputs,  # type: ignore[arg-type]
            requested_tenant_id=TENANT_B,
        )

    assert excinfo.value.code == "AUTH_FORBIDDEN"
    assert excinfo.value.status_code == 403


def test_body_supplied_user_is_rejected(membership_inputs: dict[str, object]) -> None:
    """A body/query user that disagrees with the membership is refused."""
    with pytest.raises(ApplicationError) as excinfo:
        resolve_principal(
            **membership_inputs,  # type: ignore[arg-type]
            requested_user_id=USER_B,
        )

    assert excinfo.value.code == "AUTH_FORBIDDEN"


def test_body_echoing_membership_identity_is_accepted(
    membership_inputs: dict[str, object],
) -> None:
    """Echoing the membership's own tenant/user is harmless and still derived."""
    principal = resolve_principal(
        **membership_inputs,  # type: ignore[arg-type]
        requested_tenant_id=TENANT_A,
        requested_user_id=USER_A,
    )

    assert principal.tenant_id == TENANT_A
    assert principal.user_id == USER_A


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": TENANT_B, "user_id": USER_A},
        {"tenant_id": TENANT_A, "user_id": USER_B},
    ],
)
def test_membership_must_belong_to_tenant_and_user(
    membership_inputs: dict[str, object],
    override: dict[str, str],
) -> None:
    """A membership row for another tenant/user can never mint a principal."""
    membership = {**membership_inputs["membership"], **override}  # type: ignore[arg-type]

    with pytest.raises(ApplicationError) as excinfo:
        resolve_principal(
            **{**membership_inputs, "membership": membership},  # type: ignore[arg-type]
        )

    assert excinfo.value.code == "AUTH_FORBIDDEN"


@pytest.mark.parametrize(
    ("field", "entity_id", "status"),
    [
        ("membership", None, "suspended"),
        ("user", USER_A, "disabled"),
        ("tenant", TENANT_A, "suspended"),
    ],
)
def test_inactive_entities_cannot_mint_a_principal(
    membership_inputs: dict[str, object],
    field: str,
    entity_id: str | None,
    status: str,
) -> None:
    """Suspended/disabled tenants, users and memberships fail closed."""
    inputs = dict(membership_inputs)
    if field == "membership":
        inputs["membership"] = {**inputs["membership"], "status": status}  # type: ignore[arg-type]
    else:
        assert entity_id is not None
        inputs[field] = StubEntity(id=entity_id, status=status)

    with pytest.raises(ApplicationError) as excinfo:
        resolve_principal(**inputs)  # type: ignore[arg-type]

    assert excinfo.value.code == "AUTH_FORBIDDEN"


def test_missing_membership_is_rejected(membership_inputs: dict[str, object]) -> None:
    """Without a validated membership there is no principal at all."""
    with pytest.raises(ApplicationError):
        resolve_principal(**{**membership_inputs, "membership": None})  # type: ignore[arg-type]


def test_principal_is_immutable() -> None:
    """Assigning any field on a frozen principal raises."""
    principal = make_principal(scopes=frozenset({Action.READ.value}))

    with pytest.raises(Exception) as excinfo:
        principal.tenant_id = TENANT_B  # type: ignore[misc]

    assert isinstance(excinfo.value, (AttributeError, TypeError))


def test_principal_collections_are_immutable() -> None:
    """Roles and scopes are frozen sets and reject in-place widening."""
    principal = make_principal(scopes=frozenset({Action.READ.value}))

    assert isinstance(principal.scopes, frozenset)
    with pytest.raises(AttributeError):
        principal.scopes.add(Action.ADMIN.value)  # type: ignore[attr-defined]


def test_effective_scopes_are_a_copy() -> None:
    """Reading effective scopes never hands out a mutable view of the snapshot."""
    principal = make_principal(scopes=frozenset({Action.READ.value}))

    scopes = principal.effective_scopes()
    scopes |= {Action.ADMIN.value}

    assert principal.effective_scopes() == frozenset({Action.READ.value})
    assert principal.effective_roles() == frozenset()


def test_principal_ref_exposes_only_reference_fields() -> None:
    """The serializable reference carries ids and version, nothing else."""
    principal = make_principal(
        scopes=frozenset({Action.ADMIN.value}),
        roles=frozenset({"security-admin"}),
        authorization_version=42,
    )

    ref = principal.principal_ref()

    assert ref.tenant_id == TENANT_A
    assert ref.user_id == USER_A
    assert ref.membership_id == MEMBERSHIP_A
    assert ref.authorization_version == 42
    assert ref.as_context() == {
        "tenant_id": TENANT_A,
        "user_id": USER_A,
        "membership_id": MEMBERSHIP_A,
        "authorization_version": 42,
    }
    rendered = str(ref)
    assert SESSION_ID not in rendered
    assert REQUEST_ID not in rendered
    assert "security-admin" not in rendered


def test_with_run_binds_a_run_without_mutating_the_original() -> None:
    """One principal can drive several runs through immutable copies."""
    principal = make_principal()

    bound = principal.with_run(RUN_ID)

    assert bound.run_id == RUN_ID
    assert principal.run_id is None
    assert bound is not principal
    assert bound.tenant_id == principal.tenant_id
    assert bound.membership_id == principal.membership_id
    assert bound.authorization_version == principal.authorization_version


def test_with_run_rejects_a_blank_run_id() -> None:
    """A blank run identifier is not silently accepted."""
    with pytest.raises(ApplicationError):
        make_principal().with_run("   ")


def test_with_authorization_version_returns_a_new_value() -> None:
    """Re-reading grants yields a copy, never an in-place version bump."""
    principal = make_principal(authorization_version=1)

    refreshed = principal.with_authorization_version(9)

    assert refreshed.authorization_version == 9
    assert principal.authorization_version == 1
    with pytest.raises(ApplicationError):
        principal.with_authorization_version(-1)


def test_expired_grant_contributes_no_scope(membership_inputs: dict[str, object]) -> None:
    """A grant outside its validity window is dropped during derivation."""
    now = datetime(2026, 1, 1, tzinfo=UTC)

    principal = resolve_principal(
        **membership_inputs,  # type: ignore[arg-type]
        grants=[
            RoleGrant(
                role_code="employee",
                scopes=frozenset({Action.READ.value}),
                valid_from=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            )
        ],
        now=now,
    )

    assert principal.effective_scopes() == frozenset()
    assert principal.effective_roles() == frozenset()


def test_activating_grant_contributes_scope_and_role(
    membership_inputs: dict[str, object],
) -> None:
    """An in-window grant contributes exactly its own role and scopes."""
    now = datetime(2026, 1, 1, tzinfo=UTC)

    principal = resolve_principal(
        **membership_inputs,  # type: ignore[arg-type]
        grants=[
            RoleGrant(
                role_code="policy-editor",
                scopes=frozenset({Action.READ.value, Action.EDIT.value}),
                valid_from=now - timedelta(days=1),
            )
        ],
        now=now,
    )

    assert principal.effective_roles() == frozenset({"policy-editor"})
    assert principal.effective_scopes() == frozenset({Action.READ.value, Action.EDIT.value})


def test_revoked_grant_is_dropped(membership_inputs: dict[str, object]) -> None:
    """A revoked grant loses both its role and its scopes."""
    principal = resolve_principal(
        **membership_inputs,  # type: ignore[arg-type]
        role_codes=["employee"],
        scopes=[Action.READ.value],
        grants=[
            RoleGrant(
                role_code="employee",
                scopes=frozenset({Action.READ.value}),
                revoked=True,
            )
        ],
    )

    assert principal.effective_scopes() == frozenset()
    assert principal.effective_roles() == frozenset()


def test_declared_codes_are_intersected_with_valid_grants(
    membership_inputs: dict[str, object],
) -> None:
    """Stale role/scope lists cannot survive their grants."""
    principal = resolve_principal(
        **membership_inputs,  # type: ignore[arg-type]
        role_codes=["employee", "policy-publisher"],
        scopes=[Action.READ.value, Action.PUBLISH.value],
        grants=[
            RoleGrant(role_code="employee", scopes=frozenset({Action.READ.value})),
        ],
    )

    assert principal.effective_roles() == frozenset({"employee"})
    assert principal.effective_scopes() == frozenset({Action.READ.value})


def test_authorization_version_must_be_a_non_negative_int(
    membership_inputs: dict[str, object],
) -> None:
    """The authorization version stays an int and cannot be negative."""
    with pytest.raises(ApplicationError):
        resolve_principal(**membership_inputs, authorization_version=-1)  # type: ignore[arg-type]

    principal = resolve_principal(**membership_inputs)  # type: ignore[arg-type]
    assert isinstance(principal.authorization_version, int)


# --------------------------------------------------------------------------- #
# Deliverable 2: allow-only AuthorizationService (T025)
# --------------------------------------------------------------------------- #


def test_action_enum_covers_the_contract_vocabulary() -> None:
    """The action enum is exactly the contract vocabulary and nothing more."""
    assert {action.value for action in Action} == {
        "read",
        "edit",
        "approve",
        "submit",
        "publish",
        "delete",
        "admin",
        "cross_tenant_admin",
    }


def test_ordinary_membership_cannot_reach_elevated_actions() -> None:
    """Employees are denied approve/submit/publish/delete/admin by default."""
    principal = make_principal(scopes=frozenset({Action.READ.value, Action.EDIT.value}))
    service = AuthorizationService()
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A)

    for action in (Action.APPROVE, Action.SUBMIT, Action.PUBLISH, Action.DELETE, Action.ADMIN):
        decision = service.authorize(principal, action, resource)
        assert decision.allowed is False
        assert decision.reason_code == "AUTH_MISSING_SCOPE"
        assert decision.code == "AUTH_FORBIDDEN"

    assert service.authorize(principal, Action.READ, resource).allowed is True


def test_unknown_action_is_denied() -> None:
    """An action outside the vocabulary is refused, never coerced."""
    service = AuthorizationService()
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A)

    with pytest.raises(UnknownActionError) as excinfo:
        service.authorize(make_principal(), "superuser", resource)

    assert excinfo.value.code == "AUTH_FORBIDDEN"


def test_no_grants_means_denied_everywhere() -> None:
    """Default deny: an empty grant set allows nothing at all."""
    service = AuthorizationService()
    principal = make_principal()

    for action in Action:
        decision = service.authorize(
            principal,
            action,
            ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A),
        )
        assert decision.allowed is False


def test_same_tenant_read_is_allowed() -> None:
    """A matching tenant plus an explicit read scope is allowed."""
    service = AuthorizationService()
    principal = make_principal(scopes=frozenset({Action.READ.value}))

    decision = service.authorize(
        principal,
        Action.READ,
        ResourceRef(kind="document", id="doc-1", tenant_id=TENANT_A),
    )

    assert decision.allowed is True
    assert decision.code is None
    assert decision.reason_code == "ALLOW"


def test_cross_tenant_access_is_denied_without_the_grant() -> None:
    """Tenant scope is enforced even when the action scope is present."""
    service = AuthorizationService()
    principal = make_principal(scopes=frozenset({Action.READ.value, Action.ADMIN.value}))

    decision = service.authorize(
        principal,
        Action.READ,
        ResourceRef(kind="document", id="doc-b", tenant_id=TENANT_B),
    )

    assert decision.allowed is False
    assert decision.code == "RESOURCE_NOT_FOUND"
    # The reason code is deliberately the generic not-found one: the outward
    # denial must not reveal that another tenant owns this id.
    assert decision.reason_code == "AUTH_RESOURCE_NOT_FOUND"


def test_admin_does_not_imply_cross_tenant_admin() -> None:
    """Cross-tenant reach needs its own explicit grant, never admin."""
    service = AuthorizationService()
    admin_only = make_principal(scopes=frozenset({Action.ADMIN.value}))
    resource_b = ResourceRef(kind="document", id="doc-b", tenant_id=TENANT_B)

    assert service.authorize(admin_only, Action.ADMIN, resource_b).allowed is False
    assert service.authorize(admin_only, Action.CROSS_TENANT_ADMIN, resource_b).allowed is False
    assert (
        service.authorize(
            admin_only,
            Action.ADMIN,
            ResourceRef(kind="document", id="doc-a", tenant_id=TENANT_A),
        ).allowed
        is True
    )


def test_cross_tenant_admin_grant_enables_other_tenants() -> None:
    """The explicit cross-tenant grant is what unlocks another tenant."""
    service = AuthorizationService()
    cross_admin = make_principal(
        scopes=frozenset({Action.READ.value, Action.CROSS_TENANT_ADMIN.value})
    )
    resource_b = ResourceRef(kind="document", id="doc-b", tenant_id=TENANT_B)

    assert service.authorize(cross_admin, Action.READ, resource_b).allowed is True
    # The cross-tenant grant is not itself a wildcard for tenant-local actions.
    assert service.authorize(cross_admin, Action.DELETE, resource_b).allowed is False


def test_cross_tenant_grant_is_not_implied_by_any_other_scope() -> None:
    """Only the exact cross_tenant_admin scope opens another tenant."""
    service = AuthorizationService()
    resource_b = ResourceRef(kind="document", id="doc-b", tenant_id=TENANT_B)

    for scope in ("read", "edit", "delete", "admin", "publish", "*", "tenant:*"):
        principal = make_principal(scopes=frozenset({scope, Action.READ.value}))
        assert service.authorize(principal, Action.READ, resource_b).allowed is False


def test_tenant_less_resource_needs_the_cross_tenant_grant() -> None:
    """A global object belongs to no tenant and stays unreachable for members."""
    service = AuthorizationService()
    global_resource = ResourceRef(kind="system_setting", id="setting-1")

    member = make_principal(scopes=frozenset({Action.ADMIN.value}))
    assert service.authorize(member, Action.ADMIN, global_resource).allowed is False

    cross_admin = make_principal(
        scopes=frozenset({Action.ADMIN.value, Action.CROSS_TENANT_ADMIN.value})
    )
    assert service.authorize(cross_admin, Action.ADMIN, global_resource).allowed is True


def test_decision_raise_for_denial_produces_a_typed_error() -> None:
    """Denials convert into the project's error type with the stable code."""
    service = AuthorizationService()
    principal = make_principal(scopes=frozenset({Action.READ.value}))
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A)

    decision = service.authorize(principal, Action.PUBLISH, resource)
    assert decision.denied is True
    with pytest.raises(ApplicationError) as excinfo:
        decision.raise_for_denial()
    assert excinfo.value.code == "AUTH_FORBIDDEN"

    allowed = service.authorize(principal, Action.READ, resource)
    allowed.raise_for_denial()
    assert service.raise_for_denial(principal, Action.READ, resource).allowed is True


def _denial_error(
    service: AuthorizationService,
    principal: RequestPrincipal,
    tenant_id: str,
) -> ApplicationError:
    """Authorize a resource in ``tenant_id`` that this principal cannot touch."""
    decision = service.authorize(
        principal,
        Action.PUBLISH,
        ResourceRef(kind="document", id="doc-x", tenant_id=tenant_id),
    )
    with pytest.raises(ApplicationError) as excinfo:
        decision.raise_for_denial()
    return excinfo.value


def test_nonexistent_and_other_tenant_denials_are_indistinguishable() -> None:
    """A cross-tenant probe must not be able to detect the other tenant.

    The catalogue is what makes "does not exist" observable at all: it lists one
    document in the caller's own tenant, so a genuinely absent id and another
    tenant's id are both answered as not-found with an identical shape.
    """
    catalog = InMemoryResourceCatalog(
        [ResourceRef(kind="document", id="doc-a", tenant_id=TENANT_A)]
    )
    service = AuthorizationService(resource_catalog=catalog)
    principal = make_principal(scopes=frozenset({Action.READ.value}))

    other_tenant = service.authorize(
        principal,
        Action.READ,
        ResourceRef(kind="document", id="doc-b", tenant_id=TENANT_B),
    )
    nonexistent = service.authorize(
        principal,
        Action.READ,
        ResourceRef(kind="document", id="missing", tenant_id=TENANT_A),
    )

    assert other_tenant.allowed is False
    assert nonexistent.allowed is False
    assert other_tenant.code == nonexistent.code == "RESOURCE_NOT_FOUND"
    assert other_tenant.reason_code == nonexistent.reason_code
    assert dict(other_tenant.details) == {}

    other_tenant_error = _denial_error(service, principal, TENANT_B)
    missing_error = _denial_error(service, principal, TENANT_A)
    assert other_tenant_error.code == missing_error.code
    assert other_tenant_error.status_code == missing_error.status_code
    assert other_tenant_error.message == missing_error.message


def test_tenant_visibility_hides_other_tenants() -> None:
    """Tenant visibility never confirms another tenant's existence."""
    service = AuthorizationService()
    member = make_principal(scopes=frozenset({Action.READ.value}))

    assert service.is_tenant_visible(member, TENANT_A) is True
    assert service.is_tenant_visible(member, TENANT_B) is False

    denial = service.tenant_denial(Action.READ, TENANT_B)
    assert denial.allowed is False
    assert denial.code == "TENANT_NOT_FOUND"

    cross_admin = make_principal(
        scopes=frozenset({Action.READ.value, Action.CROSS_TENANT_ADMIN.value})
    )
    assert service.is_tenant_visible(cross_admin, TENANT_B) is True


def test_authorization_version_reads_from_the_grant_source(
    grant_source: InMemoryGrantSource,
) -> None:
    """The reported version follows the grant source, not a stale snapshot."""
    service = AuthorizationService(grant_source)
    principal = make_principal(authorization_version=1)

    assert asyncio.run(service.authorization_version(principal)) == 1

    bumped = grant_source.set_grant(
        TENANT_A,
        USER_A,
        MEMBERSHIP_A,
        {Action.READ.value, Action.EDIT.value},
    )
    assert bumped == 2
    assert asyncio.run(service.authorization_version(principal)) == 2

    snapshot_only = AuthorizationService()
    assert asyncio.run(snapshot_only.authorization_version(principal)) == 1


async def test_fresh_authorization_rereads_grants(
    grant_source: InMemoryGrantSource,
) -> None:
    """authorize_fresh sees a revoked grant that the snapshot still lists."""
    service = AuthorizationService(grant_source)
    principal = make_principal(scopes=frozenset({Action.READ.value, Action.PUBLISH.value}))
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A)

    # The shared fixture only grants `read`; this test needs a live `publish`
    # grant so the pre-revocation fresh check can legitimately pass.
    grant_source.set_grant(
        TENANT_A,
        USER_A,
        MEMBERSHIP_A,
        {Action.READ.value, Action.PUBLISH.value},
    )

    assert service.authorize(principal, Action.PUBLISH, resource).allowed is True
    assert (await service.authorize_fresh(principal, Action.PUBLISH, resource)).allowed is True

    grant_source.revoke(TENANT_A, USER_A, MEMBERSHIP_A)

    snapshot_decision = service.authorize(principal, Action.PUBLISH, resource)
    fresh_decision = await service.authorize_fresh(principal, Action.PUBLISH, resource)

    assert snapshot_decision.allowed is True, "the snapshot is intentionally stale"
    assert fresh_decision.allowed is False
    assert fresh_decision.reason_code == "AUTH_MISSING_SCOPE"
    assert fresh_decision.code == "AUTH_FORBIDDEN"
    assert (await service.authorize_fresh(principal, Action.READ, resource)).allowed is False


async def test_fresh_authorization_grants_access_added_after_the_snapshot(
    grant_source: InMemoryGrantSource,
) -> None:
    """A grant added mid-request is honoured by the fresh path only."""
    service = AuthorizationService(grant_source)
    principal = make_principal(scopes=frozenset({Action.READ.value}))
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A)

    assert (await service.authorize_fresh(principal, Action.EDIT, resource)).allowed is False

    grant_source.set_grant(
        TENANT_A,
        USER_A,
        MEMBERSHIP_A,
        {Action.READ.value, Action.EDIT.value},
    )

    assert (await service.authorize_fresh(principal, Action.EDIT, resource)).allowed is True
    assert service.authorize(principal, Action.EDIT, resource).allowed is False


async def test_fresh_authorization_denies_without_a_grant_source() -> None:
    """Without a grant source nothing can be proven current, so nothing passes."""
    service = AuthorizationService()
    principal = make_principal(scopes=frozenset({Action.READ.value}))

    decision = await service.authorize_fresh(
        principal,
        Action.READ,
        ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A),
    )

    assert decision.allowed is False
    assert decision.code == "AUTH_FORBIDDEN"


async def test_fresh_authorization_drops_a_revoked_scope_only(
    grant_source: InMemoryGrantSource,
) -> None:
    """Revoking one scope keeps the remaining scopes effective."""
    grant_source.set_grant(
        TENANT_A,
        USER_A,
        MEMBERSHIP_A,
        {Action.READ.value, Action.DELETE.value},
    )
    service = AuthorizationService(grant_source)
    principal = make_principal(scopes=frozenset({Action.READ.value, Action.DELETE.value}))
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A)

    grant_source.revoke_scope(TENANT_A, USER_A, MEMBERSHIP_A, Action.DELETE.value)

    assert (await service.authorize_fresh(principal, Action.DELETE, resource)).allowed is False
    assert (await service.authorize_fresh(principal, Action.READ, resource)).allowed is True


async def test_fresh_authorization_rejects_a_foreign_membership(
    grant_source: InMemoryGrantSource,
) -> None:
    """Grants are looked up by the principal's own membership only."""
    service = AuthorizationService(grant_source)
    grant_source.set_grant(TENANT_B, USER_B, MEMBERSHIP_B, {Action.READ.value})
    foreign = make_principal(
        tenant_id=TENANT_B,
        user_id=USER_B,
        membership_id=MEMBERSHIP_B,
    )
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_B)

    assert (await service.authorize_fresh(foreign, Action.READ, resource)).allowed is True

    impostor = make_principal(scopes=frozenset({Action.READ.value}))
    assert (await service.authorize_fresh(impostor, Action.READ, resource)).allowed is False


def test_resource_ref_attributes_are_read_only() -> None:
    """Resource attributes are audit metadata and cannot be mutated in place."""
    resource = ResourceRef(
        kind="policy",
        id="policy-1",
        tenant_id=TENANT_A,
        attributes={"version": 3},
    )

    assert resource.attributes["version"] == 3
    with pytest.raises(TypeError):
        resource.attributes["version"] = 4  # type: ignore[index]

    widened = resource.with_attributes(retention="7y")
    assert widened.attributes["retention"] == "7y"
    assert "retention" not in resource.attributes
    assert str(resource) == f"policy:policy-1@{TENANT_A}"


def test_decision_objects_are_frozen() -> None:
    """A decision cannot be rewritten after it was produced."""
    service = AuthorizationService()
    decision = service.authorize(
        make_principal(),
        Action.READ,
        ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT_A),
    )

    with pytest.raises(Exception) as excinfo:
        decision.allowed = True  # type: ignore[misc]
    assert isinstance(excinfo.value, (AttributeError, TypeError))
    assert decision.retryable is False


def test_replace_can_only_rebind_a_run_never_identity() -> None:
    """dataclasses.replace on identity fields would be a caller error, not a feature."""
    principal = make_principal()
    rebound = replace(principal, run_id=RUN_ID)

    assert rebound.run_id == RUN_ID
    assert rebound.membership_id == principal.membership_id
