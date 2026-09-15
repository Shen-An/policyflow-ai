"""T033 dependency tests: principal derivation, identity claims, unit of work.

The dependency layer is the one place that decides who a request is. These tests
drive it through a real application (so error mapping is the real one) against a
migrated PostgreSQL database, because that is the only place the enforced schema
-- NOT NULL tenant, per-tenant unique identifiers, forced RLS -- actually holds.

The assertions deliberately attack the layer rather than confirm it: a client
that claims somebody else's tenant or user id must not be able to change whose
data is read, and a token whose tenant does not own its subject must be refused
rather than served from whatever row happens to share the id.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import Session, create_engine

from backend.app.api.deps import (
    PrincipalDep,
    UnitOfWorkDep,
    get_db_session,
    get_unit_of_work,
)
from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import Role, Tenant, User, UserRoleGrant
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import build_async_engine
from backend.app.main import create_app

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"
USER_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROLE_ALPHA = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GRANT_ALPHA = "dddddddd-dddd-dddd-dddd-dddddddddddd"

probe = APIRouter(prefix="/probe")
seen_units: list[int] = []


@probe.get("/principal")
async def probe_principal(principal: PrincipalDep) -> dict[str, Any]:
    """Report the derived principal so tests can inspect it."""
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "membership_id": principal.membership_id,
        "roles": sorted(principal.roles),
        "scopes": sorted(principal.scopes),
    }


@probe.get("/unit")
async def probe_unit(uow: UnitOfWorkDep) -> dict[str, Any]:
    """Report a per-request identity for the unit of work."""
    seen_units.append(id(uow))
    return {"index": len(seen_units)}


@pytest.fixture(scope="module")
def deps_url(pg_url: str) -> Iterator[str]:
    """A private migrated database containing two tenants and one membership."""
    from tests import conftest

    with conftest.scratch_database(pg_url, "pf_it_deps") as url:
        expand = conftest.alembic_upgrade(url, "001")
        assert expand.returncode == 0, expand.stderr
        backfill = conftest.run_legacy_backfill(url)
        assert backfill.returncode == 0, backfill.stderr
        head = conftest.alembic_upgrade(url, "head")
        assert head.returncode == 0, head.stderr

        import asyncio

        asyncio.run(_seed(url))
        yield url


async def _seed(url: str) -> None:
    """Insert tenants, users, one role and one membership through the ORM.

    Each level is flushed before the next is added so the inserts satisfy their
    foreign keys on the way in rather than relying on flush ordering.
    """
    engine = build_async_engine(url, None)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        for tenant_id, code in ((TENANT_ALPHA, "alpha"), (TENANT_BETA, "beta")):
            session.add(Tenant(id=tenant_id, code=code, name=code.title(), status="active"))
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

        session.add(
            Role(
                id=ROLE_ALPHA,
                tenant_id=TENANT_ALPHA,
                code="reader",
                name="Reader",
                actions=["read"],
            )
        )
        await session.flush()

        session.add(
            UserRoleGrant(
                id=GRANT_ALPHA,
                tenant_id=TENANT_ALPHA,
                user_id=USER_ALPHA,
                role_id=ROLE_ALPHA,
                scope="tenant",
            )
        )
        await session.flush()
        await session.commit()
    await engine.dispose()


@pytest.fixture()
def client(tmp_path: Path, deps_url: str) -> Iterator[TestClient]:
    """A real application whose sessions and unit of work point at PostgreSQL."""
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'deps-probe.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY="t033-secret",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t033-password",
        _env_file=None,
    )
    app: FastAPI = create_app(settings)
    app.include_router(probe)

    # The same URL drives the sync engine too: psycopg 3 serves both, whereas the
    # bare ``postgresql://`` scheme would select the absent psycopg2 driver.
    sync_engine = create_engine(deps_url)
    async_engine = build_async_engine(deps_url, None)
    factory = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)

    def _session() -> Iterator[Session]:
        with Session(sync_engine) as session:
            yield session

    async def _unit() -> AsyncIterator[UnitOfWork]:
        uow = UnitOfWork(factory=factory)
        try:
            yield uow
        finally:
            await uow.close()

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_unit_of_work] = _unit
    seen_units.clear()
    with TestClient(app) as test_client:
        yield test_client
    sync_engine.dispose()


def headers(token: str) -> dict[str, str]:
    """Return a bearer authorization header."""
    return {"Authorization": f"Bearer {token}"}


def mint(tenant_id: str, subject: str) -> str:
    """Mint a token signed with the secret the probe application verifies with."""
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR="logs",
        SECRET_KEY="t033-secret",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t033-password",
        _env_file=None,
    )
    return create_access_token(subject, settings, tenant_id=tenant_id)


def test_principal_is_derived_from_the_membership(client: TestClient) -> None:
    """The tenant, user and membership all come from the stored membership."""
    response = client.get("/probe/principal", headers=headers(mint(TENANT_ALPHA, USER_ALPHA)))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == TENANT_ALPHA
    assert body["user_id"] == USER_ALPHA
    assert body["membership_id"] == GRANT_ALPHA
    assert body["roles"] == ["reader"]
    assert "read" in body["scopes"]


def test_claimed_tenant_cannot_change_the_subject(client: TestClient) -> None:
    """A query-string tenant is compared with the membership, never trusted."""
    response = client.get(
        "/probe/principal",
        params={"tenant_id": TENANT_BETA},
        headers=headers(mint(TENANT_ALPHA, USER_ALPHA)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"
    # The refusal must not disclose the other tenant, even in the echo.
    assert TENANT_BETA not in response.text


def test_claimed_user_id_mismatch_is_rejected(client: TestClient) -> None:
    """A query-string user id that contradicts the membership is refused."""
    response = client.get(
        "/probe/principal",
        params={"user_id": USER_BETA},
        headers=headers(mint(TENANT_ALPHA, USER_ALPHA)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_claimed_identity_agreeing_with_the_membership_is_accepted(
    client: TestClient,
) -> None:
    """The claim check is a comparison, not a blanket ban on the parameters."""
    response = client.get(
        "/probe/principal",
        params={"tenant_id": TENANT_ALPHA, "user_id": USER_ALPHA},
        headers=headers(mint(TENANT_ALPHA, USER_ALPHA)),
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == USER_ALPHA


def test_token_tenant_must_own_its_subject(client: TestClient) -> None:
    """A token naming tenant beta cannot act as an alpha user of the same id.

    The user id is the caller's own input, so echoing it discloses nothing; what
    must not appear is anything the token's tenant has no claim to, such as the
    alpha membership. The refusal is a plain denial rather than a principal.
    """
    response = client.get("/probe/principal", headers=headers(mint(TENANT_BETA, USER_ALPHA)))

    assert response.status_code != 200, response.text
    assert response.json()["success"] is False
    assert GRANT_ALPHA not in response.text
    assert response.json()["error"]["code"] in {"AUTH_FORBIDDEN", "RESOURCE_NOT_FOUND"}


def test_subject_without_a_membership_is_refused(client: TestClient) -> None:
    """Owning a user row is not enough; an active membership is required."""
    response = client.get("/probe/principal", headers=headers(mint(TENANT_BETA, USER_BETA)))

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_each_request_gets_its_own_unit_of_work(client: TestClient) -> None:
    """Two requests must not share a unit of work."""
    first = client.get("/probe/unit", headers=headers(mint(TENANT_ALPHA, USER_ALPHA)))
    second = client.get("/probe/unit", headers=headers(mint(TENANT_ALPHA, USER_ALPHA)))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(seen_units) == 2
    assert seen_units[0] != seen_units[1]


def test_a_token_cannot_be_minted_without_a_tenant() -> None:
    """The tenant claim is mandatory, so no such token can exist in the first place."""
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR="logs",
        SECRET_KEY="t033-secret",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t033-password",
        _env_file=None,
    )
    with pytest.raises(ValueError):
        create_access_token(USER_ALPHA, settings, tenant_id="  ")
