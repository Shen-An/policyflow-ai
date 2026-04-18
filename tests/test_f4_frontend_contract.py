"""Frontend F4 contracts for Chat, Conversation, Feedback, and Draft APIs."""

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import (
    AuditLog,
    KnowledgeBase,
    KnowledgeDocument,
    QueryFeedback,
)
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class F4LightRAG:
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
        if "unknown" in request.query.lower():
            return []
        return [
            Evidence(
                knowledge_base_id=request.knowledge_base_ids[0],
                knowledge_base_name="HR",
                document_id="document-f4",
                document_title="Travel Policy",
                chunk_id="chunk-f4",
                snippet="Travel requests require manager approval.",
                score=0.91,
                retriever_type="lightrag",
                rank=1,
            )
        ]


class F4LLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "Travel requests require manager approval [1]."


def build_f4_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'f4-contract.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(
        settings,
        lightrag_adapter=F4LightRAG(),
        llm_service=F4LLM(),
    )


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_employee(
    client: TestClient,
    admin_headers: dict[str, str],
    username: str,
    department_code: str,
) -> dict[str, str]:
    departments = client.get("/api/departments", headers=admin_headers).json()["items"]
    department = next(item for item in departments if item["code"] == department_code)
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
            "password": "employee-password",
            "department_id": department["id"],
            "role_codes": ["employee"],
        },
    )
    assert response.status_code == 201
    return login(client, username, "employee-password")


def test_chat_conversation_and_feedback_frontend_contract(tmp_path: Path) -> None:
    app = build_f4_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        owner_headers = create_employee(client, admin_headers, "f4owner", "hr")
        other_headers = create_employee(client, admin_headers, "f4other", "finance")

        unauthenticated = client.post("/api/chat", json={"question": "Travel approval?"})
        empty_question = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": ""},
        )
        long_question = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "x" * 4001},
        )
        invalid_strategy = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "Travel approval?", "retrieval_strategy": "invalid"},
        )
        invalid_query_mode = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "Travel approval?", "query_mode": "invalid"},
        )

        chat_response = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "What is the travel approval process?"},
        )
        chat = chat_response.json()
        conversation_id = chat["conversation_id"]
        query_log_id = chat["query_log_id"]

        finance_id = next(
            item["id"]
            for item in client.get(
                "/api/knowledge-bases",
                headers=admin_headers,
            ).json()["items"]
            if item["code"] == "finance"
        )
        filtered_kb_response = client.post(
            "/api/chat",
            headers=owner_headers,
            json={
                "question": "Finance-only question",
                "knowledge_base_ids": [finance_id],
            },
        )
        no_evidence_response = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "unknown policy question"},
        )

        owner_conversation = client.get(
            f"/api/conversations/{conversation_id}",
            headers=owner_headers,
        )
        admin_conversation = client.get(
            f"/api/conversations/{conversation_id}",
            headers=admin_headers,
        )
        forbidden_conversation = client.get(
            f"/api/conversations/{conversation_id}",
            headers=other_headers,
        )
        missing_conversation = client.get(
            f"/api/conversations/{uuid4()}",
            headers=owner_headers,
        )

        feedback_response = client.post(
            f"/api/query-logs/{query_log_id}/feedback",
            headers=owner_headers,
            json={"rating": "useful", "comment": "引用准确"},
        )
        repeated_feedback = client.post(
            f"/api/query-logs/{query_log_id}/feedback",
            headers=owner_headers,
            json={"rating": "incomplete", "comment": "需要更多步骤"},
        )
        admin_feedback = client.post(
            f"/api/query-logs/{query_log_id}/feedback",
            headers=admin_headers,
            json={"rating": "useful", "comment": "Admin review"},
        )
        forbidden_feedback = client.post(
            f"/api/query-logs/{query_log_id}/feedback",
            headers=other_headers,
            json={"rating": "not_useful"},
        )
        invalid_rating = client.post(
            f"/api/query-logs/{query_log_id}/feedback",
            headers=owner_headers,
            json={"rating": "excellent"},
        )
        long_comment = client.post(
            f"/api/query-logs/{query_log_id}/feedback",
            headers=owner_headers,
            json={"rating": "useful", "comment": "x" * 1001},
        )
        missing_feedback = client.post(
            f"/api/query-logs/{uuid4()}/feedback",
            headers=owner_headers,
            json={"rating": "useful"},
        )

    assert unauthenticated.status_code == 401
    assert empty_question.status_code == 422
    assert long_question.status_code == 422
    assert invalid_strategy.status_code == 422
    assert invalid_query_mode.status_code == 422

    assert chat_response.status_code == 200
    assert query_log_id
    assert chat["citations"][0]["document_title"] == "Travel Policy"
    assert chat["confidence_score"] > 0
    assert chat["suggested_skills"][0]["name"] == "process_checklist"

    assert filtered_kb_response.status_code == 200
    assert filtered_kb_response.json()["citations"] == []
    assert filtered_kb_response.json()["confidence_score"] == 0.0
    assert filtered_kb_response.json()["compliance"]["warnings"] == [
        "NO_RELIABLE_EVIDENCE"
    ]
    assert no_evidence_response.status_code == 200
    assert no_evidence_response.json()["citations"] == []
    assert no_evidence_response.json()["confidence_score"] == 0.0

    assert owner_conversation.status_code == 200
    assert admin_conversation.status_code == 200
    assert forbidden_conversation.status_code == 403
    assert missing_conversation.status_code == 404
    assistant_message = owner_conversation.json()["messages"][1]
    metadata = assistant_message["meta_json"]
    assert metadata["query_log_id"] == query_log_id
    assert metadata["citations"] == chat["citations"]
    assert metadata["confidence_score"] == chat["confidence_score"]
    assert metadata["query_mode"] == chat["query_mode"]
    assert metadata["router_result"] == chat["router_result"]
    assert metadata["suggested_skills"] == chat["suggested_skills"]
    assert metadata["compliance"] == chat["compliance"]

    assert feedback_response.status_code == 200
    assert repeated_feedback.status_code == 200
    assert repeated_feedback.json()["id"] == feedback_response.json()["id"]
    assert repeated_feedback.json()["rating"] == "incomplete"
    assert repeated_feedback.json()["comment"] == "需要更多步骤"
    assert repeated_feedback.json()["created_at"] == feedback_response.json()["created_at"]
    assert repeated_feedback.json()["updated_at"] >= feedback_response.json()["updated_at"]
    assert admin_feedback.status_code == 200
    assert admin_feedback.json()["id"] != feedback_response.json()["id"]
    assert forbidden_feedback.status_code == 403
    assert invalid_rating.status_code == 422
    assert long_comment.status_code == 422
    assert missing_feedback.status_code == 404

    with Session(app.state.engine) as session:
        feedback_items = session.exec(select(QueryFeedback)).all()
        audit_items = session.exec(
            select(AuditLog).where(AuditLog.target_id == query_log_id)
        ).all()

    assert len(feedback_items) == 2
    assert {item.rating for item in feedback_items} == {"incomplete", "useful"}
    assert {item.action for item in audit_items} >= {
        "query_feedback.create",
        "query_feedback.update",
    }


def test_draft_frontend_contract_and_conversation_permissions(tmp_path: Path) -> None:
    app = build_f4_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        owner_headers = create_employee(client, admin_headers, "draftowner", "hr")
        other_headers = create_employee(client, admin_headers, "draftother", "finance")

        chat_response = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "Draft a travel request"},
        )
        conversation_id = chat_response.json()["conversation_id"]

        create_response = client.post(
            "/api/drafts",
            headers=owner_headers,
            json={
                "conversation_id": conversation_id,
                "draft_type": "email",
                "title": "Travel Request",
                "content": "Initial content",
                "source_question": "Draft a request",
                "related_sources": [],
            },
        )
        draft_id = create_response.json()["id"]
        second_draft = client.post(
            "/api/drafts",
            headers=owner_headers,
            json={
                "draft_type": "checklist",
                "title": "Checklist",
                "content": "- Item",
                "source_question": "Checklist",
            },
        )
        second_draft_id = second_draft.json()["id"]

        forbidden_link = client.post(
            "/api/drafts",
            headers=other_headers,
            json={
                "conversation_id": conversation_id,
                "draft_type": "email",
                "title": "Forbidden",
                "content": "Forbidden",
                "source_question": "Forbidden",
            },
        )
        missing_link = client.post(
            "/api/drafts",
            headers=owner_headers,
            json={
                "conversation_id": str(uuid4()),
                "draft_type": "email",
                "title": "Missing",
                "content": "Missing",
                "source_question": "Missing",
            },
        )
        admin_link = client.post(
            "/api/drafts",
            headers=admin_headers,
            json={
                "conversation_id": conversation_id,
                "draft_type": "summary",
                "title": "Admin Review",
                "content": "Admin content",
                "source_question": "Admin review",
            },
        )

        owner_list = client.get("/api/drafts", headers=owner_headers)
        owner_filtered = client.get(
            "/api/drafts?status=draft&draft_type=checklist&page=1&page_size=1",
            headers=owner_headers,
        )
        admin_list = client.get("/api/drafts", headers=admin_headers)
        owner_detail = client.get(f"/api/drafts/{draft_id}", headers=owner_headers)
        forbidden_detail = client.get(f"/api/drafts/{draft_id}", headers=other_headers)
        missing_detail = client.get(f"/api/drafts/{uuid4()}", headers=owner_headers)

        update_response = client.put(
            f"/api/drafts/{draft_id}",
            headers=owner_headers,
            json={"title": "Updated Travel Request", "content": "Updated content"},
        )
        confirm_response = client.post(
            f"/api/drafts/{draft_id}/confirm",
            headers=owner_headers,
        )
        update_confirmed = client.put(
            f"/api/drafts/{draft_id}",
            headers=owner_headers,
            json={"content": "Should fail"},
        )
        export_response = client.post(
            f"/api/drafts/{draft_id}/export",
            headers=owner_headers,
        )
        discard_response = client.post(
            f"/api/drafts/{second_draft_id}/discard",
            headers=owner_headers,
        )
        invalid_type = client.post(
            "/api/drafts",
            headers=owner_headers,
            json={
                "draft_type": "unknown",
                "title": "Invalid",
                "content": "Invalid",
                "source_question": "Invalid",
            },
        )

    assert create_response.status_code == 201
    assert create_response.json()["conversation_id"] == conversation_id
    assert forbidden_link.status_code == 403
    assert missing_link.status_code == 404
    assert admin_link.status_code == 201
    assert admin_link.json()["conversation_id"] == conversation_id

    assert owner_list.status_code == 200
    assert owner_list.json()["total"] == 2
    assert owner_filtered.status_code == 200
    assert owner_filtered.json()["total"] == 1
    assert owner_filtered.json()["page"] == 1
    assert owner_filtered.json()["page_size"] == 1
    assert owner_filtered.json()["items"][0]["draft_type"] == "checklist"
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 3

    assert owner_detail.status_code == 200
    assert forbidden_detail.status_code == 403
    assert missing_detail.status_code == 404
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Updated Travel Request"
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "confirmed"
    assert update_confirmed.status_code == 409
    assert update_confirmed.json()["error"]["code"] == "DRAFT_NOT_EDITABLE"
    assert export_response.status_code == 200
    assert export_response.json()["content"].startswith("# Updated Travel Request")
    assert discard_response.status_code == 200
    assert discard_response.json()["status"] == "discarded"
    assert invalid_type.status_code == 422
