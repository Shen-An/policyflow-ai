"""T069 [US2] ``/api/v2/runs`` contract, verified PG-/broker-free on SQLite.

The run-submission surface is exercised through a fully assembled application so
the error mapping, principal derivation and DurableJob enqueue are the real
ones. Only the broker/quota edges are stubbed: admission is injected via
``app.state.run_admission`` so the overload path (429/503 + ``Retry-After``) is
provable without a live Redis, and no Celery worker runs -- a submitted run
stays ``queued``, which is exactly its durable resting state before a worker
leases it.

What is genuinely asserted here: Idempotency-Key length enforcement, idempotent
re-submission returning the same run, cross-tenant read isolation, and the
overload responses carrying a numeric ``Retry-After``. What is NOT asserted (and
must not be claimed): the live RabbitMQ redelivery round-trip and the production
Redis quota decision -- those are gated in the recovery harness (T072).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.api.routes_runs import AdmissionOutcome
from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import Role, Tenant, User, UserRoleGrant
from backend.app.db.session import build_async_engine
from backend.app.jobs.service import JobService
from backend.app.main import create_app

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"
USER_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROLE_ALPHA = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ROLE_BETA = "cccccccc-cccc-cccc-cccc-ccccccccdddd"
GRANT_ALPHA = "dddddddd-dddd-dddd-dddd-dddddddddddd"
GRANT_BETA = "dddddddd-dddd-dddd-dddd-ddddddddeeee"

SECRET = "t069-secret"
GOOD_KEY = "idem-key-0123456789"  # 19 chars, within 16..128


async def _create_and_seed(url: str) -> None:
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        for tenant_id, code in ((TENANT_ALPHA, "alpha"), (TENANT_BETA, "beta")):
            session.add(
                Tenant(id=tenant_id, code=code, name=code.title(), status="active")
            )
        await session.flush()
        for user_id, tenant_id, name in (
            (USER_ALPHA, TENANT_ALPHA, "alpha-member"),
            (USER_BETA, TENANT_BETA, "beta-member"),
        ):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    username=name,
                    email=f"{name}@example.com",
                    password_hash="not-used-by-these-tests",
                    display_name=name,
                    status="active",
                )
            )
        await session.flush()
        for role_id, tenant_id in ((ROLE_ALPHA, TENANT_ALPHA), (ROLE_BETA, TENANT_BETA)):
            session.add(
                Role(
                    id=role_id,
                    tenant_id=tenant_id,
                    code="reader",
                    name="Reader",
                    actions=["read"],
                )
            )
        await session.flush()
        for grant_id, tenant_id, user_id, role_id in (
            (GRANT_ALPHA, TENANT_ALPHA, USER_ALPHA, ROLE_ALPHA),
            (GRANT_BETA, TENANT_BETA, USER_BETA, ROLE_BETA),
        ):
            session.add(
                UserRoleGrant(
                    id=grant_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role_id=role_id,
                    scope="tenant",
                )
            )
        await session.flush()
        await session.commit()
    await engine.dispose()


class _DenyAdmission:
    """Admission stub that always declines with a fixed outcome."""

    def __init__(self, outcome: AdmissionOutcome) -> None:
        self._outcome = outcome

    async def admit(self, *, resource: str, identity: str) -> AdmissionOutcome:
        return self._outcome


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_file = (tmp_path / "runs.db").as_posix()
    url = f"sqlite:///{db_file}"
    asyncio.run(_create_and_seed(url))
    settings = Settings(
        DATABASE_URL=url,
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t069-password",
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        test_client.app = app  # expose for admission injection
        yield test_client


def _headers(tenant_id: str, subject: str) -> dict[str, str]:
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR="logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t069-password",
        _env_file=None,
    )
    token = create_access_token(subject, settings, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _alpha() -> dict[str, str]:
    return _headers(TENANT_ALPHA, USER_ALPHA)


def test_enqueues_a_queued_run(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/runs",
        headers={**_alpha(), "Idempotency-Key": GOOD_KEY},
        json={"kind": "kb_reindex", "payload": {"kb": "hr"}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] == TENANT_ALPHA
    assert body["state"] == "queued"
    assert body["kind"] == "kb_reindex"
    assert body["run_id"]

    got = client.get(f"/api/v2/runs/{body['run_id']}", headers=_alpha())
    assert got.status_code == 200, got.text
    assert got.json()["run_id"] == body["run_id"]


@pytest.mark.parametrize("key", ["", "short", "x" * 15, "x" * 129])
def test_rejects_bad_idempotency_key(client: TestClient, key: str) -> None:
    headers = {**_alpha()}
    if key:
        headers["Idempotency-Key"] = key
    resp = client.post(
        "/api/v2/runs", headers=headers, json={"kind": "kb_reindex", "payload": {}}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"


def test_repeat_key_same_payload_returns_same_run(client: TestClient) -> None:
    body = {"kind": "kb_reindex", "payload": {"kb": "hr"}}
    headers = {**_alpha(), "Idempotency-Key": GOOD_KEY}
    first = client.post("/api/v2/runs", headers=headers, json=body)
    second = client.post("/api/v2/runs", headers=headers, json=body)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["run_id"] == second.json()["run_id"]


def test_repeat_key_different_payload_conflicts(client: TestClient) -> None:
    headers = {**_alpha(), "Idempotency-Key": GOOD_KEY}
    client.post("/api/v2/runs", headers=headers, json={"kind": "kb_reindex", "payload": {"kb": "hr"}})
    clash = client.post(
        "/api/v2/runs", headers=headers, json={"kind": "kb_reindex", "payload": {"kb": "finance"}}
    )
    assert clash.status_code == 409, clash.text
    assert clash.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_cross_tenant_read_is_not_found(client: TestClient) -> None:
    created = client.post(
        "/api/v2/runs",
        headers={**_alpha(), "Idempotency-Key": GOOD_KEY},
        json={"kind": "kb_reindex", "payload": {}},
    )
    run_id = created.json()["run_id"]
    other = client.get(f"/api/v2/runs/{run_id}", headers=_headers(TENANT_BETA, USER_BETA))
    assert other.status_code == 404, other.text


def test_rate_limited_returns_429_with_retry_after(client: TestClient) -> None:
    client.app.state.run_admission = _DenyAdmission(
        AdmissionOutcome(
            admitted=False,
            reason="per-tenant rate limit exceeded",
            http_status=429,
            error_code="RATE_LIMITED",
            retry_after_seconds=1.4,
        )
    )
    resp = client.post(
        "/api/v2/runs",
        headers={**_alpha(), "Idempotency-Key": GOOD_KEY},
        json={"kind": "kb_reindex", "payload": {}},
    )
    assert resp.status_code == 429, resp.text
    assert resp.headers["Retry-After"] == "2"  # ceil(1.4)
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_saturated_returns_503_with_retry_after(client: TestClient) -> None:
    client.app.state.run_admission = _DenyAdmission(
        AdmissionOutcome(
            admitted=False,
            reason="concurrency lease pool saturated",
            http_status=503,
            error_code="CONCURRENCY_SATURATED",
            retry_after_seconds=5.0,
        )
    )
    resp = client.post(
        "/api/v2/runs",
        headers={**_alpha(), "Idempotency-Key": GOOD_KEY},
        json={"kind": "kb_reindex", "payload": {}},
    )
    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "5"
    assert resp.json()["error"]["code"] == "CONCURRENCY_SATURATED"


# --------------------------------------------------------------------------- #
# Cancel contract: cooperative, tenant-scoped, idempotent, terminal-safe.
# Broker-free -- request_cancel is a durable CAS transition + outbox event; the
# worker that observes the flag between steps is gated on the broker (T064/T072).
# --------------------------------------------------------------------------- #


def _create_run(client: TestClient) -> str:
    resp = client.post(
        "/api/v2/runs",
        headers={**_alpha(), "Idempotency-Key": GOOD_KEY},
        json={"kind": "kb_reindex", "payload": {}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


def test_cancel_requests_cooperative_cancel(client: TestClient) -> None:
    run_id = _create_run(client)
    resp = client.post(f"/api/v2/runs/{run_id}/cancel", headers=_alpha())
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "cancel_requested"


def test_cancel_is_idempotent(client: TestClient) -> None:
    run_id = _create_run(client)
    first = client.post(f"/api/v2/runs/{run_id}/cancel", headers=_alpha())
    second = client.post(f"/api/v2/runs/{run_id}/cancel", headers=_alpha())
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.json()["state"] == "cancel_requested"


def test_cancel_cross_tenant_is_not_found(client: TestClient) -> None:
    run_id = _create_run(client)
    resp = client.post(
        f"/api/v2/runs/{run_id}/cancel", headers=_headers(TENANT_BETA, USER_BETA)
    )
    assert resp.status_code == 404, resp.text


def test_cancel_unknown_run_is_not_found(client: TestClient) -> None:
    resp = client.post("/api/v2/runs/does-not-exist/cancel", headers=_alpha())
    assert resp.status_code == 404, resp.text


def test_cancel_terminal_run_conflicts(client: TestClient, tmp_path: Path) -> None:
    run_id = _create_run(client)
    url = f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"

    async def _drive_to_cancelled() -> None:
        engine = build_async_engine(url, None)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = JobService(factory=factory)
        await service.request_cancel(job_id=run_id)
        await service.finalize_cancel(job_id=run_id)
        await engine.dispose()

    asyncio.run(_drive_to_cancelled())
    resp = client.post(f"/api/v2/runs/{run_id}/cancel", headers=_alpha())
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "RUN_NOT_CANCELLABLE"
