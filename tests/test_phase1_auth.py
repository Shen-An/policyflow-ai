"""Phase 1 authentication and user-management tests."""

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.security import create_access_token
from backend.app.db.models import User
from backend.app.main import create_app


def build_auth_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'auth-test.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY="test-secret-key",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(settings)


def login(client: TestClient, username: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return dict(response.json())


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_can_login_and_read_current_user(tmp_path: Path) -> None:
    app = build_auth_app(tmp_path)

    with TestClient(app) as client:
        payload = login(client, "admin", "test-password")
        response = client.get("/api/auth/me", headers=auth_headers(payload["access_token"]))

    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 1800
    assert payload["user"]["roles"] == ["sys_admin"]
    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    assert response.json()["roles"] == ["sys_admin"]


def test_invalid_credentials_return_auth_error(tmp_path: Path) -> None:
    app = build_auth_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_sys_admin_can_manage_users_and_employee_is_denied(tmp_path: Path) -> None:
    app = build_auth_app(tmp_path)

    with TestClient(app) as client:
        admin_token = login(client, "admin", "test-password")["access_token"]
        admin_headers = auth_headers(admin_token)
        departments = client.get("/api/departments", headers=admin_headers).json()["items"]
        department = next(item for item in departments if item["code"] == "hr")

        create_response = client.post(
            "/api/users",
            headers=admin_headers,
            json={
                "username": "zhangsan",
                "email": "zhangsan@example.com",
                "display_name": "张三",
                "password": "employee-password",
                "department_id": department["id"],
                "role_codes": ["employee"],
            },
        )
        user_id = create_response.json()["id"]
        list_response = client.get("/api/users?keyword=zhang", headers=admin_headers)
        role_response = client.put(
            f"/api/users/{user_id}/roles",
            headers=admin_headers,
            json={"role_codes": ["employee", "kb_admin"]},
        )

        employee_token = login(client, "zhangsan", "employee-password")["access_token"]
        denied_response = client.get("/api/users", headers=auth_headers(employee_token))

    assert create_response.status_code == 201
    assert create_response.json()["roles"] == ["employee"]
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert role_response.status_code == 200
    assert role_response.json()["roles"] == ["employee", "kb_admin"]
    assert denied_response.status_code == 403
    assert denied_response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_expired_token_is_rejected(tmp_path: Path) -> None:
    app = build_auth_app(tmp_path)

    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            admin = session.exec(select(User).where(User.username == "admin")).one()
        token = create_access_token(
            admin.id,
            app.state.settings,
            expires_delta=timedelta(seconds=-1),
        )
        response = client.get("/api/auth/me", headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_EXPIRED"
