"""T106/T107 [US1] the approval + workspace HTTP contract, through the real app.

Exercised through a fully assembled application. The store is SQLite here on
purpose: the contract under test is the HTTP edge -- body validation, status-code
mapping, principal derivation and the no-leak serializers -- none of which depend
on PostgreSQL. The approval/submission *logic* (digest, state machine, idempotency)
is proven on real PostgreSQL in the T093/T094 suites; this file proves the route
in front of it behaves.

Proven here:

* ``POST /workspaces`` selects material versions by id, returns a view with no
  object key, host path, sandbox ref or policy snapshot, and 404s a foreign version;
* ``POST /runs/{run_id}/approvals/{approval_id}`` enforces the body contract (64
  lowercase-hex digest, expected_version >= 1, reason <= 1000, decision enum) as 422;
* a digest mismatch is 409, a caller without approval authority is 403, and a
  cross-tenant decision is 404.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.approvals.digest import ActionDigestInput, compute_action_digest
from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import (
    ApprovalRequest,
    ChangeSet,
    Department,
    KnowledgeBase,
    Material,
    MaterialVersion,
    Role,
    TaskWorkspace,
    Tenant,
    User,
    UserRoleGrant,
)
from backend.app.db.session import build_async_engine

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
APPROVER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
EMPLOYEE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OTHER_USER = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SECRET = "t106-secret"


def _digest_input() -> ActionDigestInput:
    return ActionDigestInput(
        action="reimbursement_submit",
        destination="expense-system",
        source_versions=["mv-1"],
        output_versions=["draft-1"],
        file_hashes={"form.txt": "a" * 64},
        diff="x",
        evidence_set=["ev-1"],
        permission_snapshot={"tenant_id": TENANT, "user_id": APPROVER},
        side_effects=["external_submission"],
    )


async def _seed(url: str) -> str:
    """Seed the whole fixture over one aiosqlite engine; return the action digest."""
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    digest = compute_action_digest(_digest_input())
    async with factory() as session:
        session.add(Department(id="dept-1", name="HR", code="hr"))
        session.add(Tenant(id=TENANT, code="alpha", name="Alpha"))
        session.add(Tenant(id=OTHER_TENANT, code="beta", name="Beta"))
        await session.flush()
        for uid, tid, name in (
            (APPROVER, TENANT, "approver"),
            (EMPLOYEE, TENANT, "employee"),
            (OTHER_USER, OTHER_TENANT, "beta-user"),
        ):
            session.add(
                User(id=uid, tenant_id=tid, external_subject=f"{name}-sub", username=name,
                     email=f"{name}@example.test", password_hash="x", display_name=name)
            )
        session.add(Role(id="r-approver", tenant_id=TENANT, code="approver", name="Approver",
                         actions=["read", "approval:decide"]))
        session.add(Role(id="r-emp", tenant_id=TENANT, code="employee", name="Employee",
                         actions=["read"]))
        session.add(Role(id="r-beta", tenant_id=OTHER_TENANT, code="approver", name="Approver",
                         actions=["read", "approval:decide"]))
        await session.flush()
        session.add(UserRoleGrant(id="g1", tenant_id=TENANT, user_id=APPROVER,
                                  role_id="r-approver", scope="tenant"))
        session.add(UserRoleGrant(id="g2", tenant_id=TENANT, user_id=EMPLOYEE,
                                  role_id="r-emp", scope="tenant"))
        session.add(UserRoleGrant(id="g3", tenant_id=OTHER_TENANT, user_id=OTHER_USER,
                                  role_id="r-beta", scope="tenant"))
        session.add(KnowledgeBase(id="kb-1", tenant_id=TENANT, code="kb", name="HR",
                                  department_id="dept-1", rag_workspace="ws"))
        await session.flush()
        session.add(Material(id="mat-1", tenant_id=TENANT, knowledge_base_id="kb-1",
                             name="Form", source_type="user_upload", status="available"))
        session.add(MaterialVersion(id="mv-1", tenant_id=TENANT, material_id="mat-1",
                                    version_number=1, sha256="a" * 64, size_bytes=10,
                                    media_type="text/plain", status="available",
                                    created_by=EMPLOYEE))
        session.add(TaskWorkspace(id="ws-1", tenant_id=TENANT, run_id="run-1",
                                  user_id=EMPLOYEE, status="ready"))
        session.add(ChangeSet(id="cs-1", tenant_id=TENANT, workspace_id="ws-1", run_id="run-1",
                              state="awaiting_approval", side_effect_class="external_submission"))
        await session.flush()
        session.add(ApprovalRequest(id="ap-1", tenant_id=TENANT, run_id="run-1",
                                    change_set_id="cs-1", action="reimbursement_submit",
                                    destination="expense-system", action_digest=digest,
                                    requested_by=EMPLOYEE, status="pending"))
        await session.commit()
    await engine.dispose()
    return digest


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    from backend.app.main import create_app

    db_file = (tmp_path / "routes.db").as_posix()
    url = f"sqlite:///{db_file}"
    digest = asyncio.run(_seed(url))
    settings = Settings(DATABASE_URL=url, LOG_DIR=tmp_path / "logs", SECRET_KEY=SECRET,
                        ACCESS_TOKEN_EXPIRE_MINUTES=30, BOOTSTRAP_ADMIN_PASSWORD="pw",
                        _env_file=None)
    app = create_app(settings)
    with TestClient(app) as test_client:
        test_client.app = app
        test_client.digest = digest
        yield test_client


def _headers(tenant_id: str, subject: str) -> dict[str, str]:
    settings = Settings(DATABASE_URL="sqlite://", LOG_DIR="logs", SECRET_KEY=SECRET,
                        ACCESS_TOKEN_EXPIRE_MINUTES=30, BOOTSTRAP_ADMIN_PASSWORD="pw",
                        _env_file=None)
    return {"Authorization": f"Bearer {create_access_token(subject, settings, tenant_id=tenant_id)}"}


# -- workspace create (T107) -------------------------------------------------


def test_create_workspace_returns_no_infrastructure_fields(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/workspaces",
        headers=_headers(TENANT, APPROVER),
        json={"run_id": "run-1", "material_version_ids": ["mv-1"]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["inputs"][0]["material_version_id"] == "mv-1"
    serialized = resp.text.lower()
    for leak in ("sandbox_job_ref", "object_key", "policy_snapshot", "bucket", "/work/"):
        assert leak not in serialized, f"the workspace view leaks {leak!r}"


def test_selecting_a_foreign_version_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/workspaces",
        headers=_headers(OTHER_TENANT, OTHER_USER),
        json={"run_id": "run-1", "material_version_ids": ["mv-1"]},
    )
    assert resp.status_code == 404, resp.text


# -- approval decide (T106) --------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "maybe", "action_digest": "a" * 64, "expected_version": 1},
        {"decision": "approve", "action_digest": "A" * 64, "expected_version": 1},
        {"decision": "approve", "action_digest": "a" * 63, "expected_version": 1},
        {"decision": "approve", "action_digest": "a" * 64, "expected_version": 0},
        {"decision": "approve", "action_digest": "a" * 64, "expected_version": 1,
         "reason": "x" * 1001},
    ],
)
def test_malformed_decision_body_is_422(client: TestClient, body: dict) -> None:
    resp = client.post(
        "/api/v2/runs/run-1/approvals/ap-1", headers=_headers(TENANT, APPROVER), json=body
    )
    assert resp.status_code == 422, resp.text


def test_approve_succeeds_for_an_authorized_decider(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/runs/run-1/approvals/ap-1",
        headers=_headers(TENANT, APPROVER),
        json={"decision": "approve", "action_digest": client.digest, "expected_version": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"


def test_digest_mismatch_is_409(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/runs/run-1/approvals/ap-1",
        headers=_headers(TENANT, APPROVER),
        json={"decision": "approve", "action_digest": "b" * 64, "expected_version": 1},
    )
    assert resp.status_code == 409, resp.text


def test_decider_without_authority_is_403(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/runs/run-1/approvals/ap-1",
        headers=_headers(TENANT, EMPLOYEE),
        json={"decision": "approve", "action_digest": client.digest, "expected_version": 1},
    )
    assert resp.status_code == 403, resp.text


def test_cross_tenant_decision_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/runs/run-1/approvals/ap-1",
        headers=_headers(OTHER_TENANT, OTHER_USER),
        json={"decision": "approve", "action_digest": client.digest, "expected_version": 1},
    )
    assert resp.status_code == 404, resp.text
