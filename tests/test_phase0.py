"""Phase 0 acceptance tests."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.models import Department, KnowledgeBase, Role, User
from backend.app.db.session import build_engine
from backend.app.main import create_app


def build_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'policyflow-test.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )


def test_health_initializes_database_and_seed_data(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert settings.log_file.exists()

    with Session(app.state.engine) as session:
        assert len(session.exec(select(Role)).all()) == 3
        assert len(session.exec(select(Department)).all()) == 5
        assert len(session.exec(select(KnowledgeBase)).all()) == 5
        assert len(session.exec(select(User)).all()) == 1


def test_database_seed_is_idempotent(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)

    first = initialize_database(engine, settings)
    second = initialize_database(engine, settings)

    assert first.roles_created == 3
    assert first.departments_created == 5
    assert first.knowledge_bases_created == 5
    assert first.permissions_created == 10
    assert first.users_created == 1
    assert second.roles_created == 0
    assert second.departments_created == 0
    assert second.knowledge_bases_created == 0
    assert second.permissions_created == 0
    assert second.users_created == 0


def test_settings_can_be_overridden_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./overridden.db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.DATABASE_URL == "sqlite:///./overridden.db"
    assert settings.LOG_LEVEL == "DEBUG"


def test_log_file_contains_structured_json(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app):
        pass

    log_lines = settings.log_file.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in log_lines]
    assert any(payload["message"] == "Application started" for payload in payloads)
    assert all({"timestamp", "level", "logger", "message"} <= payload.keys() for payload in payloads)


def test_application_error_uses_unified_response(tmp_path: Path) -> None:
    app = create_app(build_test_settings(tmp_path))

    @app.get("/test-error")
    async def test_error() -> None:
        raise ApplicationError("TEST_ERROR", "Expected failure", 409, {"field": "value"})

    with TestClient(app) as client:
        response = client.get("/test-error")

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "error": {
            "code": "TEST_ERROR",
            "message": "Expected failure",
            "details": {"field": "value"},
        },
        "request_id": response.headers["X-Request-ID"],
    }
