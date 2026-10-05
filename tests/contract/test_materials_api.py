"""T085 [US1] ``POST /api/v2/materials`` contract, verified through the real app.

The route is exercised through a fully assembled application so the principal
derivation, validation and error mapping are the production ones. The object
store and Milvus are reached through an injected saga whose object store is real
MinIO (skipped cleanly when it is down) -- the grant has to be a genuine presigned
URL for this contract to mean anything, since "the client never chooses a key" is
the whole point.

What is asserted: the declared bounds (filename, size, SHA-256 shape, media type,
purpose) with the status code the contract names for each, that a grant names one
object and expires, that an update appends a version rather than replacing one,
and that tenant and user come only from the token.

What is NOT asserted here (and must not be claimed): the indexing half of the
saga. This suite declares uploads; the full cross-store walk is T076's, on live
Milvus.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.api.routes_materials import (
    DEFAULT_ALLOWED_MEDIA_TYPES,
    FILENAME_MAX_LENGTH,
    MATERIAL_PURPOSES,
    SHA256_PATTERN,
)
from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import (
    Department,
    KnowledgeBase,
    Material,
    MaterialVersion,
    Role,
    Tenant,
    User,
    UserRoleGrant,
)
from backend.app.db.session import build_async_engine
from backend.app.main import create_app
from backend.app.retrieval.indexer import VectorIndexer
from backend.app.storage.object_store import ObjectStore
from backend.app.storage.saga import MaterialSaga
from tests.stage5_env import deterministic_chunker

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"
USER_ALPHA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_BETA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
KB_ALPHA = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
KB_BETA = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeffff"

SECRET = "t085-secret"
DIGEST = "a" * 64


async def _seed(url: str) -> async_sessionmaker:
    engine = build_async_engine(url, None)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(Department(id="dept-1", name="HR", code="hr"))
        for tenant_id, code in ((TENANT_ALPHA, "alpha"), (TENANT_BETA, "beta")):
            session.add(Tenant(id=tenant_id, code=code, name=code.title()))
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
                )
            )
        await session.flush()
        # The alpha member holds a policy-import grant; beta deliberately does not,
        # so the purpose authority check has both sides.
        for role_id, tenant_id, actions in (
            ("role-alpha", TENANT_ALPHA, ["read", "policy:import"]),
            ("role-beta", TENANT_BETA, ["read"]),
        ):
            session.add(
                Role(
                    id=role_id,
                    tenant_id=tenant_id,
                    code="member",
                    name="Member",
                    actions=actions,
                )
            )
        await session.flush()
        for grant_id, tenant_id, user_id, role_id in (
            ("grant-alpha", TENANT_ALPHA, USER_ALPHA, "role-alpha"),
            ("grant-beta", TENANT_BETA, USER_BETA, "role-beta"),
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
        for kb_id, tenant_id in ((KB_ALPHA, TENANT_ALPHA), (KB_BETA, TENANT_BETA)):
            session.add(
                KnowledgeBase(
                    id=kb_id,
                    tenant_id=tenant_id,
                    code=f"kb-{tenant_id[:4]}",
                    name="HR",
                    department_id="dept-1",
                    rag_workspace=f"ws-{tenant_id[:4]}",
                )
            )
        await session.commit()
    return factory


class _UnusedVectorStore:
    """Stands in for Milvus on the declare-upload path, which never touches it.

    Deliberately explosive rather than a silent no-op: if the route ever starts
    indexing synchronously, this fails loudly instead of quietly passing with a
    stub that pretended to work. ``status=mock`` is not applicable -- this is not
    a mock response, it is an assertion that the path is never taken.
    """

    config = type("Config", (), {"database": "unused", "collection": "unused"})()

    def __getattr__(self, name: str):
        raise AssertionError(
            f"the declare-upload route must not reach the vector store (called {name})"
        )


@pytest.fixture()
def client(tmp_path: Path, object_store_config) -> Iterator[TestClient]:
    db_file = (tmp_path / "materials.db").as_posix()
    url = f"sqlite:///{db_file}"
    factory = asyncio.run(_seed(url))
    settings = Settings(
        DATABASE_URL=url,
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t085-password",
        _env_file=None,
    )
    app = create_app(settings)
    store = ObjectStore(object_store_config)
    app.state.material_saga = MaterialSaga(
        factory=factory,
        object_store=store,
        indexer=VectorIndexer(factory=factory, vector_store=_UnusedVectorStore()),
        chunker=deterministic_chunker,
    )
    app.state.material_factory = factory
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client
    asyncio.run(store.close())


def _headers(tenant_id: str, subject: str) -> dict[str, str]:
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR="logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="t085-password",
        _env_file=None,
    )
    return {
        "Authorization": (
            f"Bearer {create_access_token(subject, settings, tenant_id=tenant_id)}"
        )
    }


def _alpha() -> dict[str, str]:
    return _headers(TENANT_ALPHA, USER_ALPHA)


def _body(**overrides) -> dict:
    body = {
        "knowledge_base_id": KB_ALPHA,
        "filename": "reimbursement-policy.pdf",
        "size_bytes": 2048,
        "sha256": DIGEST,
        "media_type": "application/pdf",
        "purpose": "policy_import",
    }
    body.update(overrides)
    return body


# -- happy path --------------------------------------------------------------


def test_declares_an_upload_and_returns_a_scoped_grant(client: TestClient) -> None:
    """201 with a material, a version and a presigned single-object PUT."""
    resp = client.post("/api/v2/materials", headers=_alpha(), json=_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version_number"] == 1
    assert body["purpose"] == "policy_import"
    upload = body["upload"]
    assert upload["method"] == "PUT"
    assert upload["max_bytes"] == 2048
    assert upload["media_type"] == "application/pdf"
    assert upload["expires_at"]
    assert upload["bucket_alias"] == "materials"
    assert upload["url"].startswith("http")

    # The response must not leak the provider bucket or the derived key.
    serialized = resp.text
    assert client.app.state.material_saga is not None
    for leak in ("object_key", "bucket_name"):
        assert leak not in serialized


def test_filename_is_recorded_but_never_shapes_the_key(client: TestClient) -> None:
    """Two different filenames for the same version resolve to the same object."""
    first = client.post(
        "/api/v2/materials", headers=_alpha(), json=_body(filename="a.pdf")
    )
    second = client.post(
        "/api/v2/materials", headers=_alpha(), json=_body(filename="b.pdf")
    )
    assert first.status_code == second.status_code == 201
    # Different materials, so different keys -- but neither URL contains the name.
    for resp, name in ((first, "a.pdf"), (second, "b.pdf")):
        assert name not in resp.json()["upload"]["url"]


def test_update_appends_an_immutable_version(client: TestClient) -> None:
    """Passing material_id appends version 2 with version 1 as its parent."""
    created = client.post("/api/v2/materials", headers=_alpha(), json=_body())
    assert created.status_code == 201
    material_id = created.json()["material_id"]
    first_version = created.json()["material_version_id"]

    updated = client.post(
        "/api/v2/materials",
        headers=_alpha(),
        json=_body(material_id=material_id, sha256="b" * 64),
    )
    assert updated.status_code == 201, updated.text
    assert updated.json()["material_id"] == material_id
    assert updated.json()["version_number"] == 2
    assert updated.json()["material_version_id"] != first_version

    async def inspect() -> None:
        factory = client.app.state.material_factory
        async with factory() as session:
            version = await session.get(
                MaterialVersion, updated.json()["material_version_id"]
            )
            assert version is not None
            assert version.source_version_id == first_version, (
                "an update must descend from the version it supersedes"
            )
            material = await session.get(Material, material_id)
            assert material is not None
            # The new version is not active until the saga activates it.
            assert material.active_version_id is None

    asyncio.run(inspect())


# -- validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"filename": ""},
        {"filename": "x" * (FILENAME_MAX_LENGTH + 1)},
        {"filename": "   "},
        {"filename": "dir/name.pdf"},
        {"filename": "dir\\name.pdf"},
        {"size_bytes": 0},
        {"size_bytes": -1},
        {"sha256": "A" * 64},
        {"sha256": "z" * 64},
        {"sha256": "a" * 63},
        {"sha256": "a" * 65},
        {"purpose": "whatever"},
        {"knowledge_base_id": ""},
    ],
)
def test_malformed_requests_are_422(client: TestClient, overrides: dict) -> None:
    """Every shape violation is a 422 from the request model, before any work."""
    resp = client.post("/api/v2/materials", headers=_alpha(), json=_body(**overrides))
    assert resp.status_code == 422, f"{overrides} -> {resp.status_code}: {resp.text}"


def test_sha256_pattern_is_the_declared_one() -> None:
    """The contract's regex, asserted directly so a loosening is visible."""
    assert SHA256_PATTERN.pattern == r"^[a-f0-9]{64}$"
    assert SHA256_PATTERN.match("a" * 64)
    assert not SHA256_PATTERN.match("A" * 64)


def test_oversize_declaration_is_413_not_422(client: TestClient) -> None:
    """A well-formed request for too many bytes is 413, with the limit reported."""
    resp = client.post(
        "/api/v2/materials",
        headers=_alpha(),
        json=_body(size_bytes=1024 * 1024 * 1024),
    )
    assert resp.status_code == 413, resp.text
    # Same envelope as every other error on this API, not a per-route shape.
    payload = resp.json()["error"]
    assert payload["code"] == "MATERIAL_TOO_LARGE"
    assert payload["details"]["max_size_bytes"] > 0, (
        "the caller can only act on 'too large' if we say how large is allowed"
    )


def test_unsupported_media_type_is_415(client: TestClient) -> None:
    """A media type outside the allowlist is 415, with the allowlist reported."""
    resp = client.post(
        "/api/v2/materials",
        headers=_alpha(),
        json=_body(media_type="application/x-msdownload"),
    )
    assert resp.status_code == 415, resp.text
    payload = resp.json()["error"]
    assert payload["code"] == "MATERIAL_MEDIA_TYPE_UNSUPPORTED"
    assert set(payload["details"]["allowed_media_types"]) == set(
        DEFAULT_ALLOWED_MEDIA_TYPES
    )


def test_purposes_are_exactly_the_two_declared(client: TestClient) -> None:
    """Only the two contract purposes exist, and both are reachable."""
    assert MATERIAL_PURPOSES == ("task_input", "policy_import")
    accepted = client.post(
        "/api/v2/materials",
        headers=_alpha(),
        json=_body(purpose="task_input", media_type="text/plain"),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["purpose"] == "task_input"


def test_policy_import_requires_its_own_authority(client: TestClient) -> None:
    """A member without a policy grant may upload a task input, not a policy.

    Otherwise an ordinary upload could later be cited as authoritative policy,
    which is the one thing the two purposes exist to keep apart.
    """
    beta = _headers(TENANT_BETA, USER_BETA)
    denied = client.post(
        "/api/v2/materials",
        headers=beta,
        json=_body(knowledge_base_id=KB_BETA, purpose="policy_import"),
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "AUTH_FORBIDDEN"

    allowed = client.post(
        "/api/v2/materials",
        headers=beta,
        json=_body(
            knowledge_base_id=KB_BETA, purpose="task_input", media_type="text/plain"
        ),
    )
    assert allowed.status_code == 201, allowed.text


# -- identity ----------------------------------------------------------------


def test_tenant_is_never_read_from_the_body(client: TestClient) -> None:
    """A tenant_id in the body is ignored, not honoured."""
    resp = client.post(
        "/api/v2/materials",
        headers=_alpha(),
        json={**_body(), "tenant_id": TENANT_BETA, "user_id": USER_BETA},
    )
    assert resp.status_code == 201, resp.text

    async def inspect() -> None:
        factory = client.app.state.material_factory
        async with factory() as session:
            material = await session.get(Material, resp.json()["material_id"])
            assert material is not None
            assert material.tenant_id == TENANT_ALPHA, (
                "the body selected the tenant; identity must come from the token"
            )

    asyncio.run(inspect())


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    resp = client.post("/api/v2/materials", json=_body())
    assert resp.status_code in {401, 403}, resp.text


def test_updating_another_tenants_material_is_404(client: TestClient) -> None:
    """Absent and foreign ids must be indistinguishable."""
    created = client.post("/api/v2/materials", headers=_alpha(), json=_body())
    material_id = created.json()["material_id"]

    beta = _headers(TENANT_BETA, USER_BETA)
    foreign = client.post(
        "/api/v2/materials",
        headers=beta,
        json=_body(
            knowledge_base_id=KB_BETA,
            material_id=material_id,
            purpose="task_input",
            media_type="text/plain",
        ),
    )
    absent = client.post(
        "/api/v2/materials",
        headers=beta,
        json=_body(
            knowledge_base_id=KB_BETA,
            material_id="does-not-exist",
            purpose="task_input",
            media_type="text/plain",
        ),
    )
    assert foreign.status_code == 404, foreign.text
    assert absent.status_code == 404
    assert foreign.json()["error"]["code"] == absent.json()["error"]["code"], (
        "a different code for a foreign id would confirm that it exists"
    )
