"""Browser-usable knowledge-base creation and detail contract tests."""

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


def build_app(tmp_path: Path):
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'frontend-contract.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(settings)


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_frontend_can_discover_department_create_and_fetch_kb_detail(tmp_path: Path) -> None:
    app = build_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        departments_response = client.get("/api/departments", headers=admin_headers)
        options_response = client.get(
            "/api/knowledge-bases/create-options",
            headers=admin_headers,
        )
        hr_department = next(
            item for item in options_response.json()["departments"] if item["code"] == "hr"
        )
        create_response = client.post(
            "/api/knowledge-bases",
            headers=admin_headers,
            json={
                "name": "Frontend Contract KB",
                "code": "frontend-contract",
                "department_id": hr_department["id"],
                "description": "Created using browser-visible metadata",
                "default_query_mode": "hybrid",
            },
        )
        knowledge_base_id = create_response.json()["id"]
        detail_response = client.get(
            f"/api/knowledge-bases/{knowledge_base_id}",
            headers=admin_headers,
        )

        employee_create = client.post(
            "/api/users",
            headers=admin_headers,
            json={
                "username": "frontendemployee",
                "email": "frontendemployee@example.com",
                "display_name": "Frontend Employee",
                "password": "employee-password",
                "department_id": hr_department["id"],
                "role_codes": ["employee"],
            },
        )
        employee_headers = login(client, "frontendemployee", "employee-password")
        employee_detail = client.get(
            f"/api/knowledge-bases/{knowledge_base_id}",
            headers=employee_headers,
        )
        finance_id = next(
            item["id"]
            for item in client.get("/api/knowledge-bases", headers=admin_headers).json()["items"]
            if item["code"] == "finance"
        )
        denied_detail = client.get(
            f"/api/knowledge-bases/{finance_id}",
            headers=employee_headers,
        )
        invalid_id = client.get(
            "/api/knowledge-bases/not-a-uuid",
            headers=admin_headers,
        )
        missing_id = client.get(
            f"/api/knowledge-bases/{uuid4()}",
            headers=admin_headers,
        )
        denied_options = client.get(
            "/api/knowledge-bases/create-options",
            headers=employee_headers,
        )
        unauthenticated_departments = client.get("/api/departments")
        openapi = client.get("/openapi.json").json()

    assert departments_response.status_code == 200
    assert len(departments_response.json()["items"]) == 5
    assert options_response.status_code == 200
    assert hr_department["id"]
    assert create_response.status_code == 201
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == knowledge_base_id
    assert detail_response.json()["department_id"] == hr_department["id"]
    assert employee_create.status_code == 201
    assert employee_detail.status_code == 200
    assert employee_detail.json()["permission"] == "read"
    assert denied_detail.status_code == 403
    assert denied_detail.json()["error"]["code"] == "KB_ACCESS_DENIED"
    assert invalid_id.status_code == 422
    assert invalid_id.json()["error"]["code"] == "VALIDATION_ERROR"
    assert missing_id.status_code == 404
    assert missing_id.json()["error"]["code"] == "KB_NOT_FOUND"
    assert denied_options.status_code == 403
    assert unauthenticated_departments.status_code == 401
    assert "/api/departments" in openapi["paths"]
    assert "/api/knowledge-bases/create-options" in openapi["paths"]
    assert "/api/knowledge-bases/{knowledge_base_id}" in openapi["paths"]
