"""Phase 3 Skill, Draft, Tool, MCP, and Memory acceptance tests."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    Department,
    KnowledgeBase,
    KnowledgeDocument,
    MemoryItem,
    ToolCallLog,
)
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest
from backend.app.services.context_service import build_context
from backend.app.services.memory_service import write_memory


class Phase3LightRAG:
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
        return [
            Evidence(
                knowledge_base_id=request.knowledge_base_ids[0],
                knowledge_base_name="HR",
                document_id="doc-skill",
                document_title="Travel Policy",
                snippet="Travel applications require manager approval.",
                score=0.9,
                retriever_type="lightrag",
                rank=1,
            )
        ]


class Phase3LLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "差旅申请需要经理审批 [1]。"


def build_phase3_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'phase3.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(
        settings,
        lightrag_adapter=Phase3LightRAG(),
        llm_service=Phase3LLM(),
    )


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_employee(client: TestClient, app: FastAPI, admin_headers: dict[str, str]) -> str:
    with Session(app.state.engine) as session:
        department = session.exec(
            select(Department).where(Department.code == "hr")
        ).one()
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "phase3employee",
            "email": "phase3employee@example.com",
            "display_name": "Phase 3 Employee",
            "password": "employee-password",
            "department_id": department.id,
            "role_codes": ["employee"],
        },
    )
    assert response.status_code == 201
    return login(client, "phase3employee", "employee-password")


def test_phase3_end_to_end_flow(tmp_path: Path) -> None:
    app = build_phase3_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        employee_headers = headers(create_employee(client, app, admin_headers))

        skills_response = client.get("/api/skills", headers=employee_headers)
        disable_response = client.post("/api/skills/summary/disable", headers=admin_headers)
        disabled_run = client.post(
            "/api/skills/summary/run",
            headers=employee_headers,
            json={"input": {"text": "First. Second."}},
        )
        client.post("/api/skills/summary/enable", headers=admin_headers)
        skill_run = client.post(
            "/api/skills/process_checklist/run",
            headers=employee_headers,
            json={"input": {"question": "差旅申请"}},
        )
        chat_response = client.post(
            "/api/chat",
            headers=employee_headers,
            json={"question": "差旅申请流程有哪些步骤？"},
        )

        draft_response = client.post(
            "/api/drafts",
            headers=employee_headers,
            json={
                "draft_type": "email",
                "title": "Travel Request",
                "content": "Initial content",
                "source_question": "Draft an email",
                "related_sources": [],
            },
        )
        draft_id = draft_response.json()["id"]
        update_response = client.put(
            f"/api/drafts/{draft_id}",
            headers=employee_headers,
            json={"content": "Updated content"},
        )
        confirm_response = client.post(
            f"/api/drafts/{draft_id}/confirm",
            headers=employee_headers,
        )
        export_response = client.post(
            f"/api/drafts/{draft_id}/export",
            headers=employee_headers,
        )

        tool_response = client.post(
            "/api/tools/draft.create/run",
            headers=admin_headers,
            json={
                "input": {
                    "draft_type": "checklist",
                    "title": "Admin Checklist",
                    "content": "- Item",
                    "source_question": "Create checklist",
                }
            },
        )
        mcp_response = client.post(
            "/api/mcp/servers",
            headers=admin_headers,
            json={
                "name": "mock_office_tools",
                "command": "python -m backend.app.mcp.mock_server",
                "config": {"mode": "mock"},
                "enabled": True,
            },
        )
        server_id = mcp_response.json()["id"]
        health_response = client.post(
            f"/api/mcp/servers/{server_id}/health-check",
            headers=admin_headers,
        )
        mcp_call_response = client.post(
            "/api/tools/mcp.call/run",
            headers=admin_headers,
            json={
                "input": {
                    "server_id": server_id,
                    "tool_name": "mcp.email.create_draft",
                    "arguments": {"subject": "Travel"},
                }
            },
        )
        memory_response = client.post(
            "/api/tools/memory.write/run",
            headers=admin_headers,
            json={"input": {"content": "Prefers concise answers"}},
        )
        forbidden_memory = client.post(
            "/api/tools/memory.write/run",
            headers=admin_headers,
            json={"input": {"content": "制度规定必须报销"}},
        )
        logs_response = client.get("/api/tool-call-logs", headers=admin_headers)

    assert skills_response.status_code == 200
    assert len(skills_response.json()["items"]) == 7
    assert disable_response.json()["enabled"] is False
    assert disabled_run.status_code == 409
    assert disabled_run.json()["error"]["code"] == "SKILL_DISABLED"
    assert len(skill_run.json()["output"]["steps"]) == 3
    assert chat_response.json()["suggested_skills"][0]["name"] == "process_checklist"
    assert draft_response.status_code == 201
    assert update_response.json()["content"] == "Updated content"
    assert confirm_response.json()["status"] == "confirmed"
    assert export_response.json()["content"].startswith("# Travel Request")
    assert tool_response.status_code == 200
    assert health_response.json()["health_status"] == "healthy"
    assert "mcp.email.create_draft" in health_response.json()["tools"]
    assert mcp_call_response.json()["output"]["status"] == "mock"
    assert memory_response.status_code == 200
    assert forbidden_memory.status_code == 422
    assert forbidden_memory.json()["error"]["code"] == "MEMORY_POLICY_FACT_FORBIDDEN"
    assert len(logs_response.json()["items"]) >= 4

    with Session(app.state.engine) as session:
        memories = session.exec(select(MemoryItem)).all()
        logs = session.exec(select(ToolCallLog)).all()
    assert any(
        item.memory_type in {"conversation_summary", "long_term_event", "user_preference"}
        for item in memories
    )
    assert {log.status for log in logs} >= {"success", "failed"}


def test_context_keeps_memory_non_authoritative(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'memory.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        _env_file=None,
    )
    app = create_app(settings, lightrag_adapter=Phase3LightRAG(), llm_service=Phase3LLM())
    with TestClient(app):
        with Session(app.state.engine) as session:
            preference = write_memory(
                session,
                "user",
                "user-1",
                "user_preference",
                "Prefers bullet points",
            )
            preference = MemoryItem.model_validate(preference.model_dump())
            with pytest.raises(ApplicationError):
                write_memory(
                    session,
                    "user",
                    "user-1",
                    "user_preference",
                    "制度规定必须审批",
                )
    evidence = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            snippet="Authoritative policy evidence",
            retriever_type="lightrag",
            rank=1,
        )
    ]
    context = build_context(evidence, [preference], [])
    assert context["authoritative_evidence"][0]["snippet"] == "Authoritative policy evidence"
    assert context["non_authoritative_memory"][0]["content"] == "Prefers bullet points"
    assert "must not replace" in context["rules"][0]
