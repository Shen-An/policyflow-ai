"""Shared PostgreSQL environment for the Stage-6 approval/submission suites.

The approval and submission logic is pure PostgreSQL (no object store or Milvus),
so this is a lighter context than ``stage5_env``: a scratch database with the full
schema, one tenant, two users (an approver who holds authority and an employee who
does not), and one available material version to edit. It wires the approval
service, the submission service and the mock connector together with an injectable
authority check so the "fresh RBAC" and "cross-tenant" cases are exercised against
the real code rather than a stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.approvals.service import ApprovalService
from backend.app.approvals.submission import SubmissionService
from backend.app.db.models import (
    Department,
    KnowledgeBase,
    Material,
    MaterialVersion,
    TaskWorkspace,
    Tenant,
    User,
)
from backend.app.db.session import build_async_engine
from backend.app.integrations.mock_submission import MockReimbursementConnector
from tests import conftest

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
APPROVER = "11111111-1111-1111-1111-111111111111"
EMPLOYEE = "22222222-2222-2222-2222-222222222222"
TENANT_B_USER = "33333333-3333-3333-3333-333333333333"

#: Which (tenant, user, action) pairs the injected authority check allows. The
#: approver may approve and submit; the employee may do neither, so the fresh-RBAC
#: gate has a real deny case rather than an always-allow stub.
_ALLOWED: frozenset[tuple[str, str, str]] = frozenset(
    {
        (TENANT_A, APPROVER, "approve"),
        (TENANT_A, APPROVER, "submit"),
        (TENANT_B, TENANT_B_USER, "approve"),
        (TENANT_B, TENANT_B_USER, "submit"),
    }
)


@dataclass
class Stage6Env:
    factory: async_sessionmaker
    approvals: ApprovalService
    submissions: SubmissionService
    connector: MockReimbursementConnector
    material_id: str
    material_version_id: str
    workspace_id: str
    #: Mutable set so a test can revoke the approver's authority mid-flight to
    #: prove the recheck is fresh, not a snapshot.
    allowed: set[tuple[str, str, str]] = field(default_factory=set)


async def _authority_check(env_allowed: set[tuple[str, str, str]]):
    async def check(tenant_id: str, user_id: str, action: str) -> str | None:
        if (tenant_id, user_id, action) in env_allowed:
            return None
        return f"user {user_id} lacks authority to {action} in tenant {tenant_id}"

    return check


@asynccontextmanager
async def stage6_environment(
    *, pg_url: str, scratch_name: str
) -> AsyncIterator[Stage6Env]:
    """Yield a wired Stage-6 PostgreSQL environment and tear it down after."""
    stage6 = conftest.load_stage6_migration()
    stage5 = conftest.load_stage5_migration()
    with conftest.scratch_database(pg_url, scratch_name) as url:
        engine = build_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            # Apply the row-level CHECK/unique constraints the ORM does not declare,
            # rendered from the migrations so they cannot drift from production.
            for module in (stage5, stage6):
                for table, name, column, allowed in module.CHECK_CONSTRAINTS:
                    values = ", ".join(f"'{value}'" for value in allowed)
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK "
                             f"({column} IN ({values}))")
                    )
                for name, (table, predicate) in getattr(
                    module, "ROW_CHECK_CONSTRAINTS", {}
                ).items():
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate})")
                    )
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        async with factory() as session:
            session.add(Department(id="dept-1", name="HR", code="hr"))
            session.add(Tenant(id=TENANT_A, code="alpha", name="Alpha"))
            session.add(Tenant(id=TENANT_B, code="beta", name="Beta"))
            await session.commit()
        async with factory() as session:
            for user_id, tenant_id, name in (
                (APPROVER, TENANT_A, "approver"),
                (EMPLOYEE, TENANT_A, "employee"),
                (TENANT_B_USER, TENANT_B, "beta-user"),
            ):
                session.add(
                    User(
                        id=user_id,
                        tenant_id=tenant_id,
                        external_subject=f"{name}-subject",
                        display_name=name,
                        username=name,
                        email=f"{name}@example.test",
                        password_hash="not-a-real-hash",
                    )
                )
            session.add(
                KnowledgeBase(
                    id="kb-1",
                    tenant_id=TENANT_A,
                    code="kb-hr",
                    name="HR",
                    department_id="dept-1",
                    rag_workspace="ws-hr",
                )
            )
            await session.commit()
        async with factory() as session:
            material = Material(
                tenant_id=TENANT_A,
                knowledge_base_id="kb-1",
                name="Reimbursement Form",
                source_type="user_upload",
                status="available",
            )
            session.add(material)
            await session.flush()
            version = MaterialVersion(
                tenant_id=TENANT_A,
                material_id=material.id,
                version_number=1,
                sha256="a" * 64,
                size_bytes=10,
                media_type="text/plain",
                status="available",
                created_by=EMPLOYEE,
            )
            session.add(version)
            await session.commit()
            material_id, material_version_id = material.id, version.id
        async with factory() as session:
            workspace = TaskWorkspace(
                tenant_id=TENANT_A,
                run_id="run-1",
                user_id=EMPLOYEE,
                status="ready",
            )
            session.add(workspace)
            await session.commit()
            workspace_id = workspace.id

        allowed = set(_ALLOWED)
        check = await _authority_check(allowed)
        connector = MockReimbursementConnector()
        try:
            yield Stage6Env(
                factory=factory,
                approvals=ApprovalService(factory=factory, authority_check=check),
                submissions=SubmissionService(
                    factory=factory, connector=connector, authority_check=check
                ),
                connector=connector,
                material_id=material_id,
                material_version_id=material_version_id,
                workspace_id=workspace_id,
                allowed=allowed,
            )
        finally:
            await engine.dispose()
