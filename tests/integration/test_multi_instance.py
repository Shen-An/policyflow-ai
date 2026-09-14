"""Integration tests for stateless API instances over PostgreSQL.

Stage 2 moves business authority out of the process and into PostgreSQL. These
tests prove that claim rather than asserting it:

* two independently constructed API instances, each with its own engine, see the
  same committed state — so correctness does not depend on process-local locks,
  queues or caches;
* disposing one instance's engine and starting a fresh instance loses nothing;
* production configuration refuses SQLite, and production startup verifies the
  migrated schema instead of creating it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect

from backend.app.core.config import Environment, Settings
from backend.app.db.init_db import (
    SchemaAuthorityError,
    create_db_and_tables,
    initialize_database,
    verify_schema_version,
)
from backend.app.db.session import (
    build_async_engine,
    check_database_ready,
    dispose_async_engine,
    expected_schema_revision,
)
from backend.app.main import create_app
from tests import conftest


def pg_url_for_scratch() -> str:
    """Return the session PostgreSQL URL for tests that need their own database.

    Accessed through the module rather than imported by name so pytest does not
    collect the helper itself as a test.
    """
    return conftest.test_database_url()


def pg_settings(tmp_path: Path, database_url: str, name: str) -> Settings:
    """Build isolated settings for one API instance.

    Every host-local path is redirected into ``tmp_path`` so two instances never
    share workspace or upload directories: the only thing they are allowed to
    share is PostgreSQL.
    """
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    return Settings(
        ENVIRONMENT="development",
        DATABASE_URL=database_url,
        LOG_DIR=home / "logs",
        UPLOAD_DIR=home / "uploads",
        RAG_WORKSPACE_DIR=home / "rag_workspaces",
        SECRET_KEY="multi-instance-test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="multi-instance-test-password",
        _env_file=None,
    )


def login(client: TestClient, settings: Settings) -> dict[str, str]:
    """Authenticate as the bootstrap administrator and return auth headers."""
    response = client.post(
        "/api/auth/login",
        json={
            "username": settings.BOOTSTRAP_ADMIN_USERNAME,
            "password": settings.BOOTSTRAP_ADMIN_PASSWORD.get_secret_value()
            if hasattr(settings.BOOTSTRAP_ADMIN_PASSWORD, "get_secret_value")
            else settings.BOOTSTRAP_ADMIN_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def department_id(client: TestClient, headers: dict[str, str]) -> str:
    """Return a seeded department id the knowledge base can belong to."""
    response = client.get("/api/departments", headers=headers)
    assert response.status_code == 200, response.text
    departments = response.json()["items"]
    assert departments, "the seed must provide at least one department"
    return str(departments[0]["id"])


def knowledge_base_codes(client: TestClient, headers: dict[str, str]) -> set[str]:
    """Return the codes of every knowledge base visible to the caller."""
    response = client.get("/api/knowledge-bases", headers=headers)
    assert response.status_code == 200, response.text
    return {item["code"] for item in response.json()["items"]}


@pytest.fixture(scope="module")
def shared_url(pg_url: str):
    """A private, fully migrated database shared by the instances under test."""
    from tests.conftest import alembic_upgrade, run_legacy_backfill

    with conftest.scratch_database(pg_url, "pf_it_multi") as url:
        expand = alembic_upgrade(url, "001")
        assert expand.returncode == 0, expand.stderr
        backfill = run_legacy_backfill(url)
        assert backfill.returncode == 0, backfill.stderr
        head = alembic_upgrade(url, "head")
        assert head.returncode == 0, head.stderr
        yield url


def test_two_instances_share_state_through_postgresql(shared_url: str, tmp_path: Path) -> None:
    """A write through one instance must be readable through another.

    Two separate engines and two separate apps stand in for two API processes.
    Nothing is copied between them, so a visible record can only have come from
    PostgreSQL.
    """
    settings_a = pg_settings(tmp_path, shared_url, "instance-a")
    settings_b = pg_settings(tmp_path, shared_url, "instance-b")
    engine_a = create_engine(shared_url)
    engine_b = create_engine(shared_url)
    assert engine_a is not engine_b

    app_a = create_app(settings_a, database_engine=engine_a)
    app_b = create_app(settings_b, database_engine=engine_b)
    assert app_a is not app_b
    # Distinct process-local state is exactly what makes this a real test.
    assert app_a.state.engine is not app_b.state.engine

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        headers_a = login(client_a, settings_a)
        headers_b = login(client_b, settings_b)

        # Unique codes keep the assertion meaningful even if the scratch database
        # was reused after an interrupted run.
        forward_code = f"forward-{uuid4().hex[:8]}"
        reverse_code = f"reverse-{uuid4().hex[:8]}"

        before = knowledge_base_codes(client_b, headers_b)
        created = client_a.post(
            "/api/knowledge-bases",
            headers=headers_a,
            json={
                "name": "Shared Authority",
                "code": forward_code,
                "department_id": department_id(client_a, headers_a),
                "description": "created by instance A, read by instance B",
            },
        )
        assert created.status_code == 201, created.text
        assert forward_code not in before

        # Instance B never saw a request from A; it reads the committed row.
        after = knowledge_base_codes(client_b, headers_b)
        assert forward_code in after, (
            "state must live in PostgreSQL, not in the writing process"
        )

        # And the reverse direction works too, so this is not one-way luck.
        reverse = client_b.post(
            "/api/knowledge-bases",
            headers=headers_b,
            json={
                "name": "Reverse Authority",
                "code": reverse_code,
                "department_id": department_id(client_b, headers_b),
                "description": "created by instance B, read by instance A",
            },
        )
        assert reverse.status_code == 201, reverse.text
        assert reverse_code in knowledge_base_codes(client_a, headers_a)

    engine_a.dispose()
    engine_b.dispose()


def test_instance_restart_preserves_authoritative_state(shared_url: str, tmp_path: Path) -> None:
    """Restarting a single instance must not lose committed business state.

    A process that cached authority locally would lose it here; the record must
    survive because it was only ever in PostgreSQL.
    """
    writer_settings = pg_settings(tmp_path, shared_url, "instance-writer")
    writer_engine = create_engine(shared_url)
    restart_code = f"survives-{uuid4().hex[:8]}"
    app = create_app(writer_settings, database_engine=writer_engine)
    with TestClient(app) as client:
        headers = login(client, writer_settings)
        created = client.post(
            "/api/knowledge-bases",
            headers=headers,
            json={
                "name": "Survives Restart",
                "code": restart_code,
                "department_id": department_id(client, headers),
                "description": "must outlive the process that wrote it",
            },
        )
        assert created.status_code == 201, created.text

    # Simulate a restart: drop every connection the old process held.
    writer_engine.dispose()

    reader_settings = pg_settings(tmp_path, shared_url, "instance-reader")
    reader_engine = create_engine(shared_url)
    restarted = create_app(reader_settings, database_engine=reader_engine)
    try:
        with TestClient(restarted) as client:
            headers = login(client, reader_settings)
            assert restart_code in knowledge_base_codes(client, headers)
    finally:
        reader_engine.dispose()


def production_settings(**overrides: object) -> Settings:
    """Build a fully valid production configuration.

    Every production authority the settings validator demands is supplied, so a
    rejection can only be caused by the field under test rather than by some
    unrelated missing authority.
    """
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+psycopg://policyflow@postgres:5432/policyflow",
        "CELERY_BROKER_URL": "amqps://rabbitmq:5671/policyflow",
        "REDIS_URL": "rediss://redis:6379/0",
        "MILVUS_URI": "https://milvus:19530",
        "OBJECT_STORE_ENDPOINT_URL": "https://minio:9000",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318",
        "UPLOAD_DIR": "",
        "RAG_WORKSPACE_DIR": "",
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)  # type: ignore[call-arg]


def test_production_settings_reject_sqlite() -> None:
    """SQLite must be unusable as a production business authority."""
    with pytest.raises(ValidationError, match=r"DATABASE_URL.*SQLite"):
        production_settings(DATABASE_URL="sqlite:///./policyflow.db")

    # The positive case matters too: a rejection that also refuses PostgreSQL
    # would prove nothing about SQLite.
    accepted = production_settings(
        DATABASE_URL="postgresql+psycopg://policyflow@postgres:5432/policyflow"
    )
    assert accepted.ENVIRONMENT is Environment.PRODUCTION


def test_production_startup_verifies_schema_instead_of_creating_it(
    shared_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production readiness must verify the migrated schema, never create it.

    A production process that silently created tables would produce a schema
    with no migration history and therefore no rollback path, so the guard is
    asserted directly and then the read-only path is asserted to succeed.
    """
    production = production_settings(DATABASE_URL=shared_url)
    monkeypatch.setattr("backend.app.db.init_db.get_settings", lambda: production)

    engine = create_engine(shared_url)
    try:
        with pytest.raises(SchemaAuthorityError, match="not permitted in production"):
            create_db_and_tables(engine)
        # The same migrated database passes the read-only production check.
        assert verify_schema_version(engine) == expected_schema_revision()
    finally:
        engine.dispose()

    # On a database that was never migrated, startup must fail loudly and leave
    # the schema untouched rather than bootstrapping it.
    with conftest.scratch_database(pg_url_for_scratch(), "pf_it_prodguard") as unmigrated:
        guard_engine = create_engine(unmigrated)
        try:
            with pytest.raises(SchemaAuthorityError, match="not migrated"):
                initialize_database(guard_engine, production)
            assert inspect(guard_engine).get_table_names() == [], (
                "production startup must not create any table"
            )
        finally:
            guard_engine.dispose()


async def test_readiness_distinguishes_unmigrated_from_unreachable() -> None:
    """Readiness must name the real reason, because the remedies differ."""
    with conftest.scratch_database(pg_url_for_scratch(), "pf_it_unmigrated") as unmigrated_url:
        engine = build_async_engine(unmigrated_url)
        try:
            report = await check_database_ready(engine)
        finally:
            await engine.dispose()
        assert report.ok is False
        # A reachable server with no migrated schema is a distinct failure from
        # a server that cannot be reached at all.
        assert report.reason is not None and report.reason.startswith("schema is not migrated")
        assert report.schema_revision is None
        assert report.expected_revision == expected_schema_revision()

    unreachable_engine = build_async_engine(
        pg_url_for_scratch().replace("127.0.0.1:55432", "127.0.0.1:1")
    )
    try:
        unreachable = await check_database_ready(unreachable_engine)
    finally:
        await unreachable_engine.dispose()
    assert unreachable.ok is False
    assert unreachable.reason is not None and unreachable.reason.startswith(
        "database unavailable"
    )


async def test_readiness_passes_on_a_migrated_database(shared_url: str) -> None:
    """Readiness must succeed on a fully staged database."""
    engine = build_async_engine(shared_url)
    try:
        report = await check_database_ready(engine)
    finally:
        await engine.dispose()
    assert report.ok is True, report
    assert report.dialect == "postgresql"
    assert report.schema_revision == expected_schema_revision()
    assert report.expected_revision == expected_schema_revision()


@pytest.fixture(scope="module", autouse=True)
def _release_async_engine() -> None:
    """Release the module-scoped async engine so its pool does not outlive the run."""
    yield
    import asyncio

    asyncio.run(dispose_async_engine())
