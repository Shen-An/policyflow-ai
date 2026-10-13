"""T138 [US5] admin authorization matrix: tenant scope, fresh revocation, no enumeration.

The governance surface is the most tempting place to widen access, so these tests
attack it rather than confirm it:

* an ordinary member is refused (403) and the refusal is audited;
* a tenant admin cannot reach another tenant's grant, member or run -- all three
  answer the same ``404`` a missing id would;
* an admin cannot mint a scope they do not hold (no ``admin`` -> ``cross_tenant_admin``
  escalation), and platform capacity evidence is closed to them;
* a revocation is visible to the *next* request, and also to a service call made
  with a principal snapshot that still lists ``admin`` -- which is what proves the
  service re-reads grants instead of trusting the token.

The fixture is a real assembled application; see ``tests/support_admin.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.auth.principal import RequestPrincipal
from backend.app.core.exceptions import ApplicationError
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import build_async_engine
from backend.app.services.admin_service import AdminService
from tests.support_admin import (
    ADMIN,
    BETA_ADMIN,
    EMPLOYEE,
    GRANT_BETA_ADMIN,
    GRANT_EMPLOYEE,
    PLATFORM,
    ROLE_ADMIN,
    ROLE_PLATFORM,
    RUN_BETA,
    TENANT_ALPHA,
    TENANT_BETA,
    admin_client,
    bearer,
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with admin_client(tmp_path) as assembled:
        yield assembled


def _sqlite_file(client: TestClient) -> Path:
    """Return the SQLite file backing the assembled app."""
    url = str(client.app.state.settings.DATABASE_URL)
    return Path(url.split("///", 1)[1])


def _audit_rows(client: TestClient, action: str) -> list[tuple[str, str]]:
    """Return ``(action, outcome)`` rows for one action from the audit table."""
    with sqlite3.connect(_sqlite_file(client)) as conn:
        rows = conn.execute(
            "SELECT action, outcome FROM audit_events WHERE action = ?", (action,)
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


# -- allow-only scope --------------------------------------------------------


def test_admin_can_list_only_this_tenants_roles(client: TestClient) -> None:
    resp = client.get("/api/v2/admin/roles", headers=bearer(client, TENANT_ALPHA, ADMIN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    role_ids = {item["role_id"] for item in body["items"]}
    assert ROLE_ADMIN in role_ids
    assert GRANT_BETA_ADMIN not in role_ids
    assert all(item["role_id"] != "10000000-0000-0000-0000-000000000004" for item in body["items"])


def test_ordinary_member_is_refused_and_the_refusal_is_audited(client: TestClient) -> None:
    resp = client.get(
        "/api/v2/admin/roles", headers=bearer(client, TENANT_ALPHA, EMPLOYEE)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "AUTH_FORBIDDEN"
    outcomes = _audit_rows(client, "admin.role.list")
    assert ("admin.role.list", "denied") in outcomes


def test_platform_capacity_needs_the_separate_cross_tenant_scope(
    client: TestClient,
) -> None:
    denied = client.get(
        "/api/v2/admin/capacity", headers=bearer(client, TENANT_ALPHA, ADMIN)
    )
    assert denied.status_code == 403, denied.text

    allowed = client.get(
        "/api/v2/admin/capacity", headers=bearer(client, TENANT_ALPHA, PLATFORM)
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["items"][0]["llm_mode"] == "deterministic_mock"


# -- non-enumerable cross-tenant responses -----------------------------------


def test_foreign_grant_is_the_same_404_as_a_missing_one(client: TestClient) -> None:
    foreign = client.post(
        f"/api/v2/admin/grants/{GRANT_BETA_ADMIN}/revoke",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={"reason": "probe"},
    )
    missing = client.post(
        "/api/v2/admin/grants/99999999-9999-9999-9999-999999999999/revoke",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={"reason": "probe"},
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"]
    assert TENANT_BETA not in foreign.text


def test_foreign_run_audit_is_a_404(client: TestClient) -> None:
    resp = client.get(
        f"/api/v2/admin/runs/{RUN_BETA}/audit",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert resp.status_code == 404, resp.text
    assert "beta" not in resp.text.lower()


def test_granting_a_foreign_member_is_a_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/admin/grants",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={
            "user_id": BETA_ADMIN,
            "role_id": ROLE_ADMIN,
            # A body-supplied tenant must not be able to redirect the write.
            "tenant_id": TENANT_BETA,
        },
    )
    assert resp.status_code == 404, resp.text
    assert TENANT_BETA not in resp.text


# -- privilege escalation ----------------------------------------------------


def test_admin_cannot_grant_a_scope_they_do_not_hold(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/admin/grants",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={"user_id": ADMIN, "role_id": ROLE_PLATFORM},
    )
    assert resp.status_code == 403, resp.text
    assert "cross_tenant_admin" in resp.text
    archived = _audit_rows(client, "admin.grant.create")
    assert ("admin.grant.create", "denied") in archived


# -- lifecycle: create, idempotent repeat, revoke ---------------------------


def test_grant_create_is_idempotent_and_revoke_is_immediately_effective(
    client: TestClient,
) -> None:
    payload = {"user_id": EMPLOYEE, "role_code": "admin"}
    first = client.post(
        "/api/v2/admin/grants", headers=bearer(client, TENANT_ALPHA, ADMIN), json=payload
    )
    assert first.status_code == 201, first.text
    assert first.json()["created"] is True
    grant_id = first.json()["grant"]["grant_id"]

    repeat = client.post(
        "/api/v2/admin/grants", headers=bearer(client, TENANT_ALPHA, ADMIN), json=payload
    )
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["created"] is False
    assert repeat.json()["grant"]["grant_id"] == grant_id

    # The employee can now use the admin surface.
    allowed = client.get(
        "/api/v2/admin/roles", headers=bearer(client, TENANT_ALPHA, EMPLOYEE)
    )
    assert allowed.status_code == 200, allowed.text

    revoked = client.post(
        f"/api/v2/admin/grants/{grant_id}/revoke",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={"reason": "offboarding"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True
    assert revoked.json()["grant"]["revoked_at"] is not None

    # The very next request is refused: the grant was re-read, not remembered.
    denied = client.get(
        "/api/v2/admin/roles", headers=bearer(client, TENANT_ALPHA, EMPLOYEE)
    )
    assert denied.status_code == 403, denied.text

    second = client.post(
        f"/api/v2/admin/grants/{grant_id}/revoke",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={"reason": "again"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["revoked"] is False


def test_revocation_beats_a_stale_principal_snapshot(client: TestClient) -> None:
    """A principal that still claims ``admin`` must not pass a fresh check.

    The HTTP layer cannot expose this window (the token is re-resolved per
    request), so the service is called directly with a snapshot built while the
    grant was live -- then the grant is revoked, and the call must still fail.
    """
    url = str(client.app.state.settings.DATABASE_URL)
    factory = async_sessionmaker(build_async_engine(url, None), expire_on_commit=False)

    async def run() -> None:
        service = AdminService(factory=factory)
        # Grant admin to the employee on top of their existing membership. The
        # principal below keeps the *original* membership id, so the grant is
        # only visible through the union of current grants.
        async with UnitOfWork(factory=factory) as uow:
            await uow.set_tenant_context(TENANT_ALPHA)
            extra = await uow.grants.create(
                TENANT_ALPHA,
                user_id=EMPLOYEE,
                role_id=ROLE_ADMIN,
                granted_by=ADMIN,
            )
            extra_id = extra.id
            await uow.commit()

        principal = RequestPrincipal(
            tenant_id=TENANT_ALPHA,
            user_id=EMPLOYEE,
            membership_id=GRANT_EMPLOYEE,
            roles=frozenset({"employee", "admin"}),
            scopes=frozenset({"read", "admin"}),
            authorization_version=1,
            session_id="session-1",
            request_id="request-1",
        )

        # A live snapshot passes while both grants exist.
        await service.list_roles(principal)

        async with UnitOfWork(factory=factory) as uow:
            await uow.set_tenant_context(TENANT_ALPHA)
            grant = await uow.grants.get(TENANT_ALPHA, extra_id)
            await uow.grants.revoke(
                TENANT_ALPHA,
                extra_id,
                revoked_by=ADMIN,
                reason="revoked after snapshot",
                expected_version=grant.version,
            )
            await uow.commit()

        # The original membership is still active, so only the *scope* re-read
        # can explain this denial.
        with pytest.raises(ApplicationError) as exc:
            await service.list_roles(principal)
        assert exc.value.code == "AUTH_FORBIDDEN"
        assert exc.value.status_code == 403

    asyncio.run(run())
