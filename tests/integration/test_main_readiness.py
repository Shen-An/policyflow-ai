"""T034: application assembly - lifespan, readiness, unit of work, v2 surface.

These tests drive the assembled application rather than its parts, because the
defects this task exists to prevent are assembly defects: a readiness probe that
cannot tell an unmigrated database from a healthy one, a unit of work that no
request can reach, or a route that reads identity from the request.

Readiness is asserted on both dialects, because they differ on purpose. Where the
schema is migration-authoritative (PostgreSQL) a missing revision is fatal; on the
development dialect the schema is created at startup, so readiness is connectivity
plus schema presence and records no revision.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import ReadinessReport, build_async_engine, check_database_ready
from backend.app.main import create_app

ADMIN_PASSWORD = "t034-password"


def build_main_app(tmp_path: Path) -> FastAPI:
    """Return the real application on a throwaway SQLite database."""
    settings = Settings(
        DATABASE_URL=f"sqlite:///{tmp_path / 'app.db'}",
        SECRET_KEY="t034-secret",
        BOOTSTRAP_ADMIN_USERNAME="admin",
        BOOTSTRAP_ADMIN_PASSWORD=ADMIN_PASSWORD,
        LOG_DIR=str(tmp_path / "logs"),
        UPLOAD_DIR=str(tmp_path / "uploads"),
        RAG_WORKSPACE_DIR=str(tmp_path / "rag"),
        _env_file=None,
    )
    return create_app(settings)


async def _readiness(url: str) -> ReadinessReport:
    """Probe one database and release the engine it used."""
    engine = build_async_engine(url)
    try:
        return await check_database_ready(engine)
    finally:
        await engine.dispose()


def test_liveness_does_not_consult_the_database(tmp_path: Path) -> None:
    """Liveness reports the process; readiness reports the schema it can see.

    Coupling them would restart healthy instances during a database outage and
    turn one dependency's failure into a fleet-wide one.
    """
    app = build_main_app(tmp_path)

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["status"] == "ready"
    assert body["dialect"] == "sqlite"
    # The development schema is created at startup, so there is no revision to
    # report; the endpoint says so rather than implying a migrated schema.
    assert body["schema_revision"] is None
    assert body["expected_revision"]


def test_the_unit_of_work_factory_is_published_on_the_application(
    tmp_path: Path,
) -> None:
    """Requests must reach a transaction scope owned by the application.

    Two calls must not return the same object: a shared unit of work would share
    one transaction between concurrent requests.
    """
    app = build_main_app(tmp_path)

    with TestClient(app):
        first = app.state.uow_factory()
        second = app.state.uow_factory()

    assert isinstance(first, UnitOfWork)
    assert isinstance(second, UnitOfWork)
    assert first is not second


def test_v2_principal_comes_from_the_token_and_its_membership(tmp_path: Path) -> None:
    """The v2 surface serves the caller's own membership-derived identity.

    This is the only test that drives the token, the tenant-scoped dependency, the
    published unit-of-work factory and a route together over HTTP, so it is the
    one that would have caught the factory being wired into the wrong slot.
    """
    app = build_main_app(tmp_path)

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        response = client.get(
            "/api/v2/principal", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"]
    assert body["user_id"]
    assert body["membership_id"]
    # The bootstrap administrator holds sys_admin, which is what proves the stored
    # grant is being read rather than a placeholder identity being returned.
    assert "sys_admin" in body["roles"]
    assert body["authorization_version"] >= 1


def test_v2_principal_rejects_a_request_that_names_a_foreign_tenant(
    tmp_path: Path,
) -> None:
    """Identity must not be acceptable from the query string.

    A caller that names a tenant it does not belong to must be refused, so the
    token stays the only source of tenant identity.
    """
    app = build_main_app(tmp_path)

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        own = client.get("/api/v2/principal", headers=headers).json()["tenant_id"]
        foreign = "22222222-2222-2222-2222-222222222222"
        assert foreign != own
        response = client.get(f"/api/v2/principal?tenant_id={foreign}", headers=headers)

    assert response.status_code != 200, response.text
    assert response.json()["error"]["code"] in {"AUTH_FORBIDDEN", "PERMISSION_DENIED"}
    assert own not in response.text


@pytest.fixture(scope="module")
def migrated_pg_url(pg_url: str) -> Iterator[str]:
    """A PostgreSQL database migrated all the way to head."""
    from tests import conftest

    with conftest.scratch_database(pg_url, "pf_it_ready_ok") as url:
        conftest.alembic_upgrade(url, "001")
        conftest.run_legacy_backfill(url)
        conftest.alembic_upgrade(url, "head")
        yield url


@pytest.fixture(scope="module")
def empty_pg_url(pg_url: str) -> Iterator[str]:
    """A PostgreSQL database that exists but carries no schema at all."""
    from tests import conftest

    with conftest.scratch_database(pg_url, "pf_it_ready_empty") as url:
        yield url


def test_readiness_is_authoritative_on_a_migrated_postgresql_database(
    migrated_pg_url: str,
) -> None:
    """A migrated PostgreSQL database is ready, and says which revision it is on."""
    report = asyncio.run(_readiness(migrated_pg_url))

    assert report.ok is True
    assert report.dialect == "postgresql"
    assert report.schema_revision is not None
    assert report.schema_revision == report.expected_revision


def test_readiness_refuses_an_unmigrated_postgresql_database(empty_pg_url: str) -> None:
    """Where the schema is migration-authoritative, an absent revision is fatal.

    This is the gap T034 exists to close: before it, the health endpoint returned
    200 on a database that had never been migrated.
    """
    report = asyncio.run(_readiness(empty_pg_url))

    assert report.ok is False
    assert report.dialect == "postgresql"
    assert report.schema_revision is None
    assert "migrated" in (report.reason or "")
