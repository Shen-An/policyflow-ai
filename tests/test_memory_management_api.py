"""Memory management API tests."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, MemoryItem, User
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest
from backend.app.services.memory_service import (
    MEMORY_TYPE_LONG_TERM,
    MEMORY_TYPE_PREFERENCE,
)


class MemoryMgmtLightRAG:
    name = "lightrag"

    @property
    def available(self) -> bool:
        return True

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        return None

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return []


class MemoryMgmtLLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "ok"


def build_app(tmp_path: Path):
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'memory-mgmt.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(
        settings,
        lightrag_adapter=MemoryMgmtLightRAG(),
        llm_service=MemoryMgmtLLM(),
    )


def login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def test_list_and_delete_memory(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        token = login(client)
        headers = {"Authorization": f"Bearer {token}"}
        with Session(app.state.engine) as session:
            user = session.get(User, client.get("/api/auth/me", headers=headers).json()["id"])
            assert user is not None
            tenant_id = user.tenant_id
            # Seed rows directly with the tenant stamped, so the read/delete API -
            # which is what this test exercises - runs against realistic data. A
            # same-tenant memory owned by another user proves owner-level
            # invisibility within the tenant.
            session.add_all(
                [
                    MemoryItem(
                        tenant_id=tenant_id,
                        owner_type="user",
                        owner_id=user.id,
                        memory_type=MEMORY_TYPE_PREFERENCE,
                        content="Prefers concise answers",
                        source="manual",
                    ),
                    MemoryItem(
                        tenant_id=tenant_id,
                        owner_type="user",
                        owner_id=user.id,
                        memory_type=MEMORY_TYPE_LONG_TERM,
                        content="Discussed travel budget last week",
                        source="manual",
                    ),
                    MemoryItem(
                        tenant_id=tenant_id,
                        owner_type="user",
                        owner_id="other-user",
                        memory_type=MEMORY_TYPE_PREFERENCE,
                        content="Should not be visible",
                        source="manual",
                    ),
                ]
            )
            session.commit()

        listed = client.get("/api/memory", headers=headers)
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["total"] == 2
        assert all(item["content"] != "Should not be visible" for item in payload["items"])

        filtered = client.get(
            "/api/memory",
            headers=headers,
            params={"memory_type": "user_preference"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["memory_type"] == "user_preference"

        memory_id = filtered.json()["items"][0]["id"]
        deleted = client.delete(f"/api/memory/{memory_id}", headers=headers)
        assert deleted.status_code == 204

        after = client.get(
            "/api/memory",
            headers=headers,
            params={"memory_type": "user_preference"},
        )
        assert after.json()["total"] == 0

        missing = client.delete(f"/api/memory/{memory_id}", headers=headers)
        assert missing.status_code == 404
