"""Shared fixture builders for the Stage 9 / US5 administration suites.

The governance surface is exercised through a fully assembled application, so the
fixtures have to create the same shape production has: two tenants, an admin and
an employee in each, one platform operator holding the separate
``cross_tenant_admin`` scope, plus the run, job, quota, usage and drift rows the
dashboards read.

Rows are inserted before ``create_app`` so the application's startup sees an
already-seeded database; the SQLite engine keeps the suites runnable on hosts
without PostgreSQL, while the same assertions also run on PostgreSQL (see
``POLICYFLOW_TEST_DATABASE_URL``) where the enterprise schema is enforced.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import (
    AgentRun,
    AuditEvent,
    CapacityTestRun,
    DurableJob,
    QuotaLease,
    QuotaPolicy,
    ReconciliationIssue,
    Role,
    RunEvent,
    Tenant,
    UsageRecord,
    User,
    UserRoleGrant,
)
from backend.app.db.session import build_async_engine

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"

ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
EMPLOYEE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BETA_ADMIN = "cccccccc-cccc-cccc-cccc-cccccccccccc"
PLATFORM = "dddddddd-dddd-dddd-dddd-dddddddddddd"

ROLE_ADMIN = "10000000-0000-0000-0000-000000000001"
ROLE_EMPLOYEE = "10000000-0000-0000-0000-000000000002"
ROLE_PLATFORM = "10000000-0000-0000-0000-000000000003"
ROLE_BETA_ADMIN = "10000000-0000-0000-0000-000000000004"

GRANT_ADMIN = "20000000-0000-0000-0000-000000000001"
GRANT_EMPLOYEE = "20000000-0000-0000-0000-000000000002"
GRANT_PLATFORM = "20000000-0000-0000-0000-000000000003"
GRANT_BETA_ADMIN = "20000000-0000-0000-0000-000000000004"

RUN_ALPHA = "30000000-0000-0000-0000-000000000001"
RUN_ALPHA_2 = "30000000-0000-0000-0000-000000000011"
RUN_ALPHA_3 = "30000000-0000-0000-0000-000000000021"
RUN_BETA = "30000000-0000-0000-0000-000000000002"

JOB_ALPHA = "40000000-0000-0000-0000-000000000001"
JOB_TERMINAL = "40000000-0000-0000-0000-000000000002"
JOB_QUEUED = "40000000-0000-0000-0000-000000000003"

ISSUE_ALPHA = "50000000-0000-0000-0000-000000000001"
ISSUE_OPEN = "50000000-0000-0000-0000-000000000003"
ISSUE_BETA = "50000000-0000-0000-0000-000000000002"

SECRET = "stage9-admin-secret"
API_KEY_VALUE = "sk-live-should-never-leak"


def _settings(url: str, tmp_path: Path) -> Settings:
    """Build test settings that keep logs and the schema inside ``tmp_path``."""
    return Settings(
        DATABASE_URL=url,
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="pw",
        _env_file=None,
    )


def headers(tenant_id: str, subject: str, tmp_path: Path) -> dict[str, str]:
    """Return a bearer header for one membership of one tenant."""
    token = create_access_token(
        subject, _settings("sqlite://", tmp_path), tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed(url: str) -> None:
    """Insert the two-tenant governance fixture."""
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(Tenant(id=TENANT_ALPHA, code="alpha", name="Alpha"))
        session.add(Tenant(id=TENANT_BETA, code="beta", name="Beta"))
        await session.flush()

        for user_id, tenant_id, name in (
            (ADMIN, TENANT_ALPHA, "alpha-admin"),
            (EMPLOYEE, TENANT_ALPHA, "alpha-employee"),
            (PLATFORM, TENANT_ALPHA, "alpha-platform"),
            (BETA_ADMIN, TENANT_BETA, "beta-admin"),
        ):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    external_subject=f"{name}-sub",
                    username=name,
                    email=f"{name}@example.test",
                    password_hash="x",
                    display_name=name,
                )
            )
        await session.flush()

        session.add(Role(id=ROLE_ADMIN, tenant_id=TENANT_ALPHA, code="admin",
                         name="Administrator", actions=["read", "admin"]))
        session.add(Role(id=ROLE_EMPLOYEE, tenant_id=TENANT_ALPHA, code="employee",
                         name="Employee", actions=["read"]))
        session.add(Role(id=ROLE_PLATFORM, tenant_id=TENANT_ALPHA, code="platform_admin",
                         name="Platform administrator",
                         actions=["read", "admin", "cross_tenant_admin"]))
        session.add(Role(id=ROLE_BETA_ADMIN, tenant_id=TENANT_BETA, code="admin",
                         name="Administrator", actions=["read", "admin"]))
        await session.flush()

        for grant_id, tenant_id, user_id, role_id in (
            (GRANT_ADMIN, TENANT_ALPHA, ADMIN, ROLE_ADMIN),
            (GRANT_EMPLOYEE, TENANT_ALPHA, EMPLOYEE, ROLE_EMPLOYEE),
            (GRANT_PLATFORM, TENANT_ALPHA, PLATFORM, ROLE_PLATFORM),
            (GRANT_BETA_ADMIN, TENANT_BETA, BETA_ADMIN, ROLE_BETA_ADMIN),
        ):
            session.add(
                UserRoleGrant(
                    id=grant_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role_id=role_id,
                    scope="tenant",
                    valid_from=now - timedelta(days=1),
                )
            )
        await session.flush()

        for index, (run_id, tenant_id, user_id, thread) in enumerate(
            (
                (RUN_ALPHA, TENANT_ALPHA, ADMIN, "thread-alpha"),
                (RUN_ALPHA_2, TENANT_ALPHA, ADMIN, "thread-alpha-2"),
                (RUN_ALPHA_3, TENANT_ALPHA, ADMIN, "thread-alpha-3"),
                (RUN_BETA, TENANT_BETA, BETA_ADMIN, "thread-beta"),
            )
        ):
            session.add(
                AgentRun(
                    id=f"ar-{index}",
                    run_id=run_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    kind="chat",
                    thread_id=thread,
                    status="succeeded",
                    evidence_gate="supported",
                    created_at=now - timedelta(minutes=10 - index),
                )
            )
        await session.flush()

        # Two durable milestones so the admin timeline can be shown to merge
        # audit rows and run events in time order.
        session.add(RunEvent(id="re-alpha-1", event_id="70000000-0000-0000-0000-000000000001",
                             tenant_id=TENANT_ALPHA, run_id=RUN_ALPHA, sequence=1,
                             event_type="run.started", stage="validate",
                             public_status="running", payload={"stage": "validate"}))
        session.add(RunEvent(id="re-alpha-2", event_id="70000000-0000-0000-0000-000000000002",
                             tenant_id=TENANT_ALPHA, run_id=RUN_ALPHA, sequence=2,
                             event_type="run.completed", stage="finalize",
                             public_status="succeeded", payload={"answer": "grounded"}))

        session.add(
            AuditEvent(
                id="ae-alpha-1",
                event_id="60000000-0000-0000-0000-000000000001",
                tenant_id=TENANT_ALPHA,
                run_id=RUN_ALPHA,
                actor_ref=ADMIN,
                action="approval.decide",
                resource_kind="approval_request",
                resource_id="ap-1",
                outcome="allowed",
                reason_code=None,
                redacted_metadata={
                    "api_key": API_KEY_VALUE,
                    "note": "approved after review",
                    "path": r"C:\Users\secret\policy.pdf",
                },
            )
        )
        session.add(
            AuditEvent(
                id="ae-beta-1",
                event_id="60000000-0000-0000-0000-000000000002",
                tenant_id=TENANT_BETA,
                run_id=RUN_BETA,
                actor_ref=BETA_ADMIN,
                action="approval.decide",
                resource_kind="approval_request",
                resource_id="ap-beta-1",
                outcome="allowed",
                redacted_metadata={"note": "beta only"},
            )
        )

        session.add(DurableJob(id=JOB_ALPHA, tenant_id=TENANT_ALPHA, run_id=RUN_ALPHA,
                               kind="document_index", idempotency_key="idem-alpha-0001",
                               payload_digest="a" * 64, state="recoverable_failed",
                               attempts=1, max_attempts=3, available_at=now,
                               last_error_code="INDEX_TIMEOUT", version=3))
        session.add(DurableJob(id=JOB_TERMINAL, tenant_id=TENANT_ALPHA,
                               run_id="30000000-0000-0000-0000-000000000003",
                               kind="document_index", idempotency_key="idem-alpha-0002",
                               payload_digest="b" * 64, state="terminal_failed",
                               attempts=5, max_attempts=5, available_at=now,
                               version=6))
        session.add(DurableJob(id=JOB_QUEUED, tenant_id=TENANT_ALPHA,
                               run_id="30000000-0000-0000-0000-000000000004",
                               kind="eval_run", idempotency_key="idem-alpha-0003",
                               payload_digest="c" * 64, state="queued",
                               attempts=0, max_attempts=3,
                               available_at=now - timedelta(minutes=3),
                               version=1))

        session.add(QuotaPolicy(id="qp-global", scope="global", tenant_id=None,
                                workload="chat", requests_per_window=1000,
                                window_seconds=60, max_concurrency=50, version=1))
        session.add(QuotaPolicy(id="qp-alpha-v1", scope="tenant",
                                tenant_id=TENANT_ALPHA, workload="chat",
                                requests_per_window=10, window_seconds=60,
                                max_concurrency=2, version=1))
        session.add(QuotaPolicy(id="qp-alpha-v2", scope="tenant",
                                tenant_id=TENANT_ALPHA, workload="chat",
                                requests_per_window=100, window_seconds=60,
                                max_concurrency=8, version=2))
        session.add(QuotaPolicy(id="qp-beta", scope="tenant",
                                tenant_id=TENANT_BETA, workload="chat",
                                requests_per_window=999, window_seconds=60,
                                max_concurrency=99, version=1))

        session.add(UsageRecord(id="ur-alpha-1", tenant_id=TENANT_ALPHA,
                                run_id=RUN_ALPHA, user_id=ADMIN, kind="chat",
                                provider="mock", model="deterministic",
                                requests=3, reserved_tokens=200, actual_tokens=120))
        session.add(UsageRecord(id="ur-beta-1", tenant_id=TENANT_BETA,
                                run_id=RUN_BETA, user_id=BETA_ADMIN, kind="chat",
                                requests=99, actual_tokens=9999))
        session.add(QuotaLease(id="ql-alpha", tenant_id=TENANT_ALPHA, owner="alpha-lease",
                               resource="chat", expires_at=now + timedelta(minutes=5)))
        session.add(QuotaLease(id="ql-beta", tenant_id=TENANT_BETA, owner="beta-lease",
                               resource="chat", expires_at=now + timedelta(minutes=5)))

        session.add(ReconciliationIssue(
            id=ISSUE_ALPHA, tenant_id=TENANT_ALPHA, store_pair="postgres_object",
            issue_kind="missing_object", resource_kind="material_version",
            resource_id="mv-alpha-1", version_id="v1", severity="critical",
            state="manual_required", attempts=5, max_attempts=5,
            last_error_code="OBJECT_MISSING",
        ))
        session.add(ReconciliationIssue(
            id=ISSUE_OPEN, tenant_id=TENANT_ALPHA, store_pair="postgres_milvus",
            issue_kind="missing_chunk", resource_kind="material_version",
            resource_id="mv-alpha-2", version_id="v1", severity="critical",
            state="open", attempts=0, max_attempts=5,
        ))
        session.add(ReconciliationIssue(
            id=ISSUE_BETA, tenant_id=TENANT_BETA, store_pair="postgres_milvus",
            issue_kind="orphan_vector", resource_kind="material_version",
            resource_id="mv-beta-1", version_id="v1", severity="critical",
            state="open", attempts=1, max_attempts=5,
        ))

        session.add(
            CapacityTestRun(
                id="cap-1",
                commit_sha="abc123",
                scenario="saturation",
                suite_version="1.0",
                environment_manifest={"os": "windows"},
                topology={"api": 1},
                hardware={"cpu": 8},
                dataset_manifest={"docs": 10},
                llm_mode="deterministic_mock",
                target_profile={"users": 1000},
                raw_artifact_uri="artifacts/acceptance/load/run.csv",
                raw_artifact_sha256="c" * 64,
                summary_metrics={"p95_ms": 120},
                verdict="pass",
                known_limits="deterministic mock only",
            )
        )
        await session.commit()
    await engine.dispose()


@contextmanager
def admin_client(tmp_path: Path) -> Iterator[TestClient]:
    """Seed the fixture and yield a client for a fully assembled application."""
    from backend.app.main import create_app

    db_file = (tmp_path / "admin.db").as_posix()
    url = f"sqlite:///{db_file}"
    asyncio.run(_seed(url))
    settings = _settings(url, tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        client.app = app
        yield client


def bearer(client: TestClient, tenant_id: str, subject: str) -> dict[str, str]:
    """Return a bearer header using the client's own tmp path for its settings."""
    base = client.app.state.settings
    token = create_access_token(subject, base, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}
