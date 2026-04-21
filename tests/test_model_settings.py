"""Independent runtime Chat and Embedding settings acceptance tests."""

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import AuditLog, ModelProvider
from backend.app.main import create_app


def build_app(tmp_path: Path):
    return create_app(Settings(DATABASE_URL=f"sqlite:///{(tmp_path / 'model-settings.db').as_posix()}", LOG_DIR=tmp_path / "logs", SECRET_KEY="model-settings-secret", BOOTSTRAP_ADMIN_PASSWORD="test-password", _env_file=None))


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_employee(client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    department = client.get("/api/departments", headers=admin_headers).json()["items"][0]
    response = client.post("/api/users", headers=admin_headers, json={"username": "settingsemployee", "email": "settingsemployee@example.com", "display_name": "Settings Employee", "password": "employee-password", "department_id": department["id"], "role_codes": ["employee"]})
    assert response.status_code == 201
    return login(client, "settingsemployee", "employee-password")


def test_chat_and_embedding_use_independent_secure_providers(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    chat_requests: list[dict[str, object]] = []
    embedding_requests: list[dict[str, object]] = []

    async def chat_handler(request: httpx.Request) -> httpx.Response:
        chat_requests.append({"path": request.url.path, "authorization": request.headers.get("Authorization"), "body": json.loads(request.content) if request.content else None})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "chat-a"}]})
        return httpx.Response(200, json={"output_text": "OK"})

    async def embedding_handler(request: httpx.Request) -> httpx.Response:
        embedding_requests.append({"path": request.url.path, "authorization": request.headers.get("Authorization"), "body": json.loads(request.content) if request.content else None})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "embed-b"}]})
        embedding_attempts = sum(
            1 for item in embedding_requests if str(item["path"]).endswith("/embeddings")
        )
        if embedding_attempts == 1:
            return httpx.Response(
                500,
                headers={"nvcf-reqid": "nvidia-attempt-1"},
                json={"error": "Something went wrong with the request."},
            )
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    chat_client = httpx.AsyncClient(transport=httpx.MockTransport(chat_handler))
    embedding_client = httpx.AsyncClient(transport=httpx.MockTransport(embedding_handler))
    with TestClient(app) as client:
        app.state.llm_service._client = chat_client
        app.state.embedding_service._client = embedding_client
        admin = login(client, "admin", "test-password")
        employee = create_employee(client, admin)
        assert client.get("/api/settings/model-providers").status_code == 401
        assert client.get("/api/settings/model-providers", headers=employee).status_code == 403
        assert client.get("/api/settings/model-providers", headers=admin).json() == {"chat": None, "embedding": None}
        chat_saved = client.put("/api/settings/model-providers/chat", headers={**admin, "X-Request-ID": "save-chat"}, json={"name": "company-a-chat", "base_url": "https://chat.company-a.com/v1/responses", "auth_mode": "bearer", "api_style": "openai_responses", "api_key": "chat-secret", "model": "chat-a", "timeout_seconds": 30, "enabled": True})
        embedding_saved = client.put("/api/settings/model-providers/embedding", headers={**admin, "X-Request-ID": "save-embedding"}, json={"name": "company-b-embedding", "base_url": "https://integrate.api.nvidia.com/v1", "auth_mode": "bearer", "api_style": "openai_embeddings", "api_key": "embedding-secret", "model": "embed-b", "embedding_dimension": 3, "embedding_input_type": "query", "timeout_seconds": 60, "enabled": True})
        settings_response = client.get("/api/settings/model-providers", headers=admin)
        chat_models = client.get("/api/settings/model-providers/chat/models", headers=admin)
        embedding_models = client.get("/api/settings/model-providers/embedding/models", headers=admin)
        chat_test = client.post("/api/settings/model-providers/chat/test", headers=admin)
        embedding_test = client.post("/api/settings/model-providers/embedding/test", headers=admin)
        invalid = client.put("/api/settings/model-providers/other", headers=admin, json={})

    import asyncio
    asyncio.run(chat_client.aclose())
    asyncio.run(embedding_client.aclose())
    assert chat_saved.status_code == 200 and embedding_saved.status_code == 200
    assert chat_saved.json()["base_url"] == "https://chat.company-a.com/v1/responses"
    assert embedding_saved.json()["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert embedding_saved.json()["embedding_dimension"] == 3
    assert "chat-secret" not in chat_saved.text and "embedding-secret" not in embedding_saved.text
    assert settings_response.json()["chat"]["name"] == "company-a-chat"
    assert settings_response.json()["embedding"]["name"] == "company-b-embedding"
    assert chat_models.json() == {"capability": "chat", "models": ["chat-a"]}
    assert embedding_models.json() == {"capability": "embedding", "models": ["embed-b"]}
    assert chat_test.json()["result"]["status"] == "passed"
    assert embedding_test.json()["result"]["dimension"] == 3
    assert invalid.status_code == 422
    assert len(chat_requests) == 2
    assert chat_requests[0]["path"].endswith("/v1/models")
    assert chat_requests[1]["path"].endswith("/v1/responses")
    assert chat_requests[1]["authorization"] == "Bearer chat-secret"
    assert chat_requests[1]["body"]["model"] == "chat-a"
    assert chat_requests[1]["body"]["input"] == "Connectivity test"
    assert embedding_requests[0]["authorization"] == "Bearer embedding-secret"
    assert embedding_requests[1]["authorization"] == "Bearer embedding-secret"
    assert embedding_requests[1]["body"]["model"] == "embed-b"
    assert embedding_requests[1]["body"]["input_type"] == "query"
    assert embedding_requests[1]["body"]["encoding_format"] == "float"
    assert embedding_requests[1]["body"]["truncate"] == "NONE"
    assert "input_type" not in embedding_requests[2]["body"]
    assert embedding_requests[2]["body"]["truncate"] == "NONE"
    with Session(app.state.engine) as session:
        providers = session.exec(select(ModelProvider)).all()
        audits = session.exec(select(AuditLog).where(AuditLog.action == "settings.model_provider.update")).all()
    assert {provider.capability for provider in providers} == {"chat", "embedding"}
    serialized = json.dumps([provider.model_dump(mode="json") for provider in providers])
    assert "chat-secret" not in serialized and "embedding-secret" not in serialized
    assert {audit.request_id for audit in audits} == {"save-chat", "save-embedding"}
