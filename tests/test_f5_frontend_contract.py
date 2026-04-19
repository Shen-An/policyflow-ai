"""Frontend F5 contracts for Audit, FAQ, and Evaluation APIs."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import (
    AuditLog,
    EvalCase,
    KnowledgeBase,
    KnowledgeDocument,
    RetrievalEvalItem,
)
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class F5LightRAG:
    name = "lightrag"

    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []

    @property
    def available(self) -> bool:
        return True

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        self.documents.append(
            {
                "knowledge_base_id": knowledge_base.id,
                "knowledge_base_name": knowledge_base.name,
                "document_id": document.id,
                "document_title": document.title,
                "document_version": document.source_version,
                "snippet": document.content_text or "",
            }
        )

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        if "force failure" in request.query.lower():
            raise RuntimeError("forced retrieval failure")
        return [
            Evidence(
                knowledge_base_id=str(item["knowledge_base_id"]),
                knowledge_base_name=str(item["knowledge_base_name"]),
                document_id=str(item["document_id"]),
                document_title=str(item["document_title"]),
                document_version=int(item["document_version"]),
                snippet=str(item["snippet"]),
                score=0.95,
                retriever_type="lightrag",
                rank=index,
            )
            for index, item in enumerate(self.documents, start=1)
            if item["knowledge_base_id"] in request.knowledge_base_ids
        ][:limit]


class F5LLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "Annual leave requires manager approval [1]."


def build_f5_app(tmp_path: Path, backend: F5LightRAG | None = None) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'f5-contract.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(
        settings,
        lightrag_adapter=backend or F5LightRAG(),
        llm_service=F5LLM(),
    )


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_user(
    client: TestClient,
    admin_headers: dict[str, str],
    username: str,
    role_codes: list[str],
    department_code: str = "hr",
) -> tuple[str, dict[str, str]]:
    departments = client.get("/api/departments", headers=admin_headers).json()["items"]
    department = next(item for item in departments if item["code"] == department_code)
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": f"{username} display",
            "password": "user-password",
            "department_id": department["id"],
            "role_codes": role_codes,
        },
    )
    assert response.status_code == 201
    return response.json()["id"], login(client, username, "user-password")


def get_kb_id(
    client: TestClient,
    headers: dict[str, str],
    code: str,
) -> str:
    items = client.get("/api/knowledge-bases", headers=headers).json()["items"]
    return next(item["id"] for item in items if item["code"] == code)


def upload_and_index_document(
    client: TestClient,
    headers: dict[str, str],
    knowledge_base_id: str,
    title: str = "Leave Policy",
) -> str:
    upload = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        headers=headers,
        files={
            "file": (
                "leave.txt",
                b"Annual leave requires manager approval. Submit requests in advance.",
                "text/plain",
            )
        },
        data={"title": title},
    )
    assert upload.status_code == 201
    document_id = upload.json()["document_id"]
    index_response = client.post(
        f"/api/documents/{document_id}/index",
        headers=headers,
    )
    assert index_response.status_code == 200
    return document_id


def test_audit_contract_permissions_filters_redaction_and_request_id(tmp_path: Path) -> None:
    app = build_f5_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        employee_id, employee_headers = create_user(
            client,
            admin_headers,
            "f5audituser",
            ["employee"],
        )
        now = datetime.now(UTC)
        with Session(app.state.engine) as session:
            older = AuditLog(
                actor_id=employee_id,
                action="audit.contract",
                target_type="document",
                target_id=str(uuid4()),
                detail={
                    "password": "plain",
                    "nested": {
                        "access_token": "token-value",
                        "safe": "visible",
                        "items": [
                            {"Authorization": "Bearer secret"},
                            {"api_key_value": "key-value"},
                        ],
                    },
                },
                request_id="request-older",
                created_at=now - timedelta(minutes=1),
            )
            newer = AuditLog(
                actor_id=employee_id,
                action="audit.contract",
                target_type="knowledge_base",
                target_id=str(uuid4()),
                detail={"safe": "newer"},
                request_id="request-newer",
                created_at=now,
            )
            session.add(older)
            session.add(newer)
            session.commit()
            older_id = older.id

        unauthenticated = client.get("/api/audit-logs")
        forbidden = client.get("/api/audit-logs", headers=employee_headers)
        invalid_page = client.get(
            "/api/audit-logs?page=0",
            headers=admin_headers,
        )
        invalid_actor = client.get(
            "/api/audit-logs?actor_id=not-a-uuid",
            headers=admin_headers,
        )
        filtered = client.get(
            (
                "/api/audit-logs?action=audit.contract"
                f"&actor_id={employee_id}&page=1&page_size=1"
            ),
            headers=admin_headers,
        )
        target_filtered = client.get(
            "/api/audit-logs?action=audit.contract&target_type=document",
            headers=admin_headers,
        )
        detail = client.get(
            f"/api/audit-logs/{older_id}",
            headers=admin_headers,
        )
        missing = client.get(
            f"/api/audit-logs/{uuid4()}",
            headers=admin_headers,
        )
        malformed = client.get(
            "/api/audit-logs/not-a-uuid",
            headers=admin_headers,
        )
        health = client.get("/health", headers={"X-Request-ID": "frontend-f5-request"})

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert invalid_page.status_code == 422
    assert invalid_actor.status_code == 422
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert filtered.json()["page"] == 1
    assert filtered.json()["page_size"] == 1
    assert filtered.json()["items"][0]["request_id"] == "request-newer"
    assert target_filtered.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["actor"] == {
        "id": employee_id,
        "username": "f5audituser",
        "display_name": "f5audituser display",
    }
    assert detail.json()["detail"] == {
        "password": "[REDACTED]",
        "nested": {
            "access_token": "[REDACTED]",
            "safe": "visible",
            "items": [
                {"Authorization": "[REDACTED]"},
                {"api_key_value": "[REDACTED]"},
            ],
        },
    }
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "AUDIT_LOG_NOT_FOUND"
    assert malformed.status_code == 422
    assert health.headers["X-Request-ID"] == "frontend-f5-request"


def test_faq_contract_permissions_sources_state_machine_and_audit(tmp_path: Path) -> None:
    app = build_f5_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        _, employee_headers = create_user(
            client,
            admin_headers,
            "f5faqemployee",
            ["employee"],
        )
        _, kb_admin_headers = create_user(
            client,
            admin_headers,
            "f5faqadmin",
            ["kb_admin"],
        )
        hr_id = get_kb_id(client, admin_headers, "hr")
        document_id = upload_and_index_document(client, admin_headers, hr_id)

        unauthenticated = client.get("/api/faq-drafts")
        forbidden = client.get("/api/faq-drafts", headers=employee_headers)
        generate = client.post(
            "/api/faq-drafts",
            headers={**kb_admin_headers, "X-Request-ID": "faq-generate-request"},
            json={
                "knowledge_base_id": hr_id,
                "source_document_id": document_id,
                "count": 3,
            },
        )
        first_faq, second_faq, third_faq = generate.json()["items"]
        approve = client.post(
            f"/api/faq-drafts/{first_faq['id']}/approve",
            headers={**kb_admin_headers, "X-Request-ID": "faq-approve-request"},
        )
        repeated_approve = client.post(
            f"/api/faq-drafts/{first_faq['id']}/approve",
            headers=kb_admin_headers,
        )
        reject_after_approve = client.post(
            f"/api/faq-drafts/{first_faq['id']}/reject",
            headers=kb_admin_headers,
            json={"reason": "Cannot reject approved FAQ"},
        )
        reject = client.post(
            f"/api/faq-drafts/{second_faq['id']}/reject",
            headers={**kb_admin_headers, "X-Request-ID": "faq-reject-request"},
            json={"reason": "Duplicate topic"},
        )
        repeated_reject = client.post(
            f"/api/faq-drafts/{second_faq['id']}/reject",
            headers=kb_admin_headers,
            json={"reason": "Again"},
        )
        pending_filter = client.get(
            "/api/faq-drafts?status=draft",
            headers=kb_admin_headers,
        )
        rejected_filter = client.get(
            "/api/faq-drafts?status=rejected",
            headers=kb_admin_headers,
        )
        missing = client.post(
            f"/api/faq-drafts/{uuid4()}/approve",
            headers=kb_admin_headers,
        )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert generate.status_code == 201
    assert generate.headers["X-Request-ID"] == "faq-generate-request"
    assert first_faq["knowledge_base_name"]
    assert first_faq["source_document_title"] == "Leave Policy"
    assert first_faq["source_conversation_id"] is None
    assert approve.status_code == 200
    assert approve.json()["faq_draft"]["status"] == "approved"
    assert repeated_approve.status_code == 409
    assert repeated_approve.json()["error"]["code"] == "FAQ_STATUS_INVALID"
    assert reject_after_approve.status_code == 409
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"
    assert repeated_reject.status_code == 409
    assert pending_filter.status_code == 200
    assert [item["id"] for item in pending_filter.json()["items"]] == [third_faq["id"]]
    assert [item["id"] for item in rejected_filter.json()["items"]] == [second_faq["id"]]
    assert missing.status_code == 404

    with Session(app.state.engine) as session:
        faq_audits = session.exec(
            select(AuditLog).where(
                AuditLog.action.in_(["faq.generate", "faq.approve", "faq.reject"])
            )
        ).all()

    assert [item.action for item in faq_audits].count("faq.generate") == 3
    request_ids = {item.action: item.request_id for item in faq_audits}
    assert request_ids["faq.generate"] == "faq-generate-request"
    assert request_ids["faq.approve"] == "faq-approve-request"
    assert request_ids["faq.reject"] == "faq-reject-request"


def test_eval_contract_validation_scope_permissions_and_openapi(tmp_path: Path) -> None:
    app = build_f5_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        _, employee_headers = create_user(
            client,
            admin_headers,
            "f5evalemployee",
            ["employee"],
        )
        _, kb_admin_headers = create_user(
            client,
            admin_headers,
            "f5evaladmin",
            ["kb_admin"],
        )
        hr_id = get_kb_id(client, admin_headers, "hr")
        finance_id = get_kb_id(client, admin_headers, "finance")
        hr_document_id = upload_and_index_document(client, admin_headers, hr_id)
        finance_document_id = upload_and_index_document(
            client,
            admin_headers,
            finance_id,
            "Finance Policy",
        )
        case = client.post(
            "/api/eval/cases",
            headers=kb_admin_headers,
            json={
                "question": "How is annual leave approved?",
                "category": "hr",
                "expected_answer_keywords": ["manager approval"],
                "expected_source_documents": ["Leave Policy"],
                "should_answer": True,
            },
        )

        unauthenticated = client.get("/api/eval/runs")
        forbidden = client.get("/api/eval/runs", headers=employee_headers)
        empty_run = client.post(
            "/api/eval/runs",
            headers=kb_admin_headers,
            json={"name": "Empty run"},
        )
        invalid_type = client.post(
            "/api/eval/runs",
            headers=kb_admin_headers,
            json={
                "name": "Invalid type",
                "case_ids": [case.json()["id"]],
                "eval_types": ["unsupported"],
            },
        )
        invalid_top_k = client.post(
            "/api/eval/runs",
            headers=kb_admin_headers,
            json={
                "name": "Invalid top k",
                "case_ids": [case.json()["id"]],
                "eval_types": ["rag_answer"],
                "retrieval_config": {"top_k_values": [0, 101]},
            },
        )
        missing_case = client.post(
            "/api/eval/runs",
            headers=kb_admin_headers,
            json={
                "name": "Missing case",
                "case_ids": [str(uuid4())],
                "eval_types": ["rag_answer"],
            },
        )
        missing_kb_item = client.post(
            "/api/eval/retrieval-items",
            headers=kb_admin_headers,
            json={
                "query": "Missing KB",
                "knowledge_base_ids": [str(uuid4())],
            },
        )
        invalid_document_scope = client.post(
            "/api/eval/retrieval-items",
            headers=kb_admin_headers,
            json={
                "eval_case_id": case.json()["id"],
                "query": "Leave approval",
                "knowledge_base_ids": [hr_id],
                "relevant_document_ids": [finance_document_id],
            },
        )
        retrieval_item = client.post(
            "/api/eval/retrieval-items",
            headers=kb_admin_headers,
            json={
                "eval_case_id": case.json()["id"],
                "query": "Leave approval",
                "knowledge_base_ids": [hr_id],
                "relevant_document_ids": [hr_document_id],
            },
        )
        debug_unauthenticated = client.post(
            "/api/eval/retrieval-debug",
            json={"query": "leave", "knowledge_base_ids": [hr_id]},
        )
        debug_forbidden = client.post(
            "/api/eval/retrieval-debug",
            headers=employee_headers,
            json={"query": "leave", "knowledge_base_ids": [hr_id]},
        )
        debug_missing_kb = client.post(
            "/api/eval/retrieval-debug",
            headers=kb_admin_headers,
            json={"query": "leave", "knowledge_base_ids": [str(uuid4())]},
        )
        debug_success = client.post(
            "/api/eval/retrieval-debug",
            headers=kb_admin_headers,
            json={"query": "leave", "knowledge_base_ids": [hr_id]},
        )
        malformed_run = client.get(
            "/api/eval/runs/not-a-uuid",
            headers=kb_admin_headers,
        )
        missing_run = client.get(
            f"/api/eval/runs/{uuid4()}",
            headers=kb_admin_headers,
        )
        openapi = client.get("/openapi.json").json()

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert empty_run.status_code == 422
    assert invalid_type.status_code == 422
    assert invalid_top_k.status_code == 422
    assert missing_case.status_code == 404
    assert missing_case.json()["error"]["code"] == "EVAL_CASE_NOT_FOUND"
    assert missing_kb_item.status_code == 404
    assert invalid_document_scope.status_code == 422
    assert invalid_document_scope.json()["error"]["code"] == "EVAL_DOCUMENT_SCOPE_INVALID"
    assert retrieval_item.status_code == 201
    assert debug_unauthenticated.status_code == 401
    assert debug_forbidden.status_code == 403
    assert debug_missing_kb.status_code == 404
    assert debug_success.status_code == 200
    assert debug_success.json()["items"][0]["document_title"] == "Leave Policy"
    assert malformed_run.status_code == 422
    assert missing_run.status_code == 404
    assert "/api/audit-logs" in openapi["paths"]
    assert "/api/audit-logs/{audit_log_id}" in openapi["paths"]
    assert "/api/faq-drafts" in openapi["paths"]
    assert "/api/eval/runs" in openapi["paths"]
    assert "/api/eval/runs/{run_id}" in openapi["paths"]

    with Session(app.state.engine) as session:
        disabled_case = session.get(EvalCase, case.json()["id"])
        disabled_item = session.get(RetrievalEvalItem, retrieval_item.json()["id"])
        assert disabled_case is not None
        assert disabled_item is not None
        disabled_case.enabled = False
        disabled_item.enabled = False
        session.add(disabled_case)
        session.add(disabled_item)
        session.commit()

    with TestClient(app) as client:
        kb_admin_headers = login(client, "f5evaladmin", "user-password")
        disabled_case_run = client.post(
            "/api/eval/runs",
            headers=kb_admin_headers,
            json={
                "name": "Disabled case",
                "case_ids": [case.json()["id"]],
                "eval_types": ["rag_answer"],
            },
        )
        disabled_item_run = client.post(
            "/api/eval/runs",
            headers=kb_admin_headers,
            json={
                "name": "Disabled item",
                "retrieval_item_ids": [retrieval_item.json()["id"]],
                "eval_types": ["retrieval"],
            },
        )

    assert disabled_case_run.status_code == 409
    assert disabled_case_run.json()["error"]["code"] == "EVAL_CASE_DISABLED"
    assert disabled_item_run.status_code == 409
    assert disabled_item_run.json()["error"]["code"] == "RETRIEVAL_EVAL_ITEM_DISABLED"


def test_eval_async_history_answer_skipped_and_failed_lifecycle(tmp_path: Path) -> None:
    backend = F5LightRAG()
    app = build_f5_app(tmp_path, backend)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        hr_id = get_kb_id(client, admin_headers, "hr")
        document_id = upload_and_index_document(client, admin_headers, hr_id)
        answer_case = client.post(
            "/api/eval/cases",
            headers=admin_headers,
            json={
                "question": "How is annual leave approved?",
                "category": "hr",
                "expected_answer_keywords": ["manager approval"],
                "expected_source_documents": ["Leave Policy"],
                "should_answer": True,
            },
        ).json()
        answer_run_response = client.post(
            "/api/eval/runs",
            headers={**admin_headers, "X-Request-ID": "eval-answer-request"},
            json={
                "name": "Answer evaluation",
                "case_ids": [answer_case["id"]],
                "eval_types": ["rag_answer", "ragas"],
                "retrieval_config": {"top_k_values": [5, 1, 5, 3]},
                "ragas_config": {"enabled": False, "metrics": ["faithfulness"]},
            },
        )
        answer_run = client.get(
            f"/api/eval/runs/{answer_run_response.json()['id']}",
            headers=admin_headers,
        )

        skipped_item = client.post(
            "/api/eval/retrieval-items",
            headers=admin_headers,
            json={
                "query": "Leave without labelled ground truth",
                "knowledge_base_ids": [hr_id],
                "relevant_document_ids": [],
            },
        ).json()
        skipped_run_response = client.post(
            "/api/eval/runs",
            headers=admin_headers,
            json={
                "name": "Skipped evaluation",
                "retrieval_item_ids": [skipped_item["id"]],
                "eval_types": ["retrieval"],
            },
        )
        skipped_run = client.get(
            f"/api/eval/runs/{skipped_run_response.json()['id']}",
            headers=admin_headers,
        )

        failed_item = client.post(
            "/api/eval/retrieval-items",
            headers=admin_headers,
            json={
                "query": "Force failure",
                "knowledge_base_ids": [hr_id],
                "relevant_document_ids": [document_id],
            },
        ).json()
        failed_run_response = client.post(
            "/api/eval/runs",
            headers=admin_headers,
            json={
                "name": "Failed evaluation",
                "retrieval_item_ids": [failed_item["id"]],
                "eval_types": ["retrieval"],
            },
        )
        failed_run = client.get(
            f"/api/eval/runs/{failed_run_response.json()['id']}",
            headers=admin_headers,
        )
        history = client.get(
            "/api/eval/runs?page=1&page_size=2",
            headers=admin_headers,
        )
        success_history = client.get(
            "/api/eval/runs?status=success",
            headers=admin_headers,
        )
        creator_history = client.get(
            f"/api/eval/runs?created_by={answer_run.json()['created_by']}",
            headers=admin_headers,
        )

    assert answer_run_response.status_code == 201
    assert answer_run_response.json()["status"] == "pending"
    assert answer_run_response.headers["X-Request-ID"] == "eval-answer-request"
    assert answer_run.status_code == 200
    answer_payload = answer_run.json()
    assert answer_payload["status"] == "success"
    assert answer_payload["request_id"] == "eval-answer-request"
    assert answer_payload["created_by"]
    assert answer_payload["created_at"]
    assert answer_payload["started_at"]
    assert answer_payload["finished_at"]
    assert answer_payload["config_snapshot"]["retrieval_config"]["top_k_values"] == [1, 3, 5]
    answer_result = answer_payload["results"][0]
    assert answer_result["answer"] == "Annual leave requires manager approval [1]."
    assert answer_result["answer_metrics"] == {
        "answer_accuracy": 1.0,
        "citation_hit_rate": 1.0,
        "no_answer_refusal_rate": 0.0,
    }
    assert answer_result["type_statuses"] == {
        "rag_answer": "completed",
        "ragas": "skipped",
    }
    assert answer_result["ragas_metrics"]["reason"] == "disabled"

    assert skipped_run_response.json()["status"] == "pending"
    assert skipped_run.json()["status"] == "skipped"
    assert skipped_run.json()["metrics"]["skipped_cases"] == 1
    assert skipped_run.json()["results"][0]["type_statuses"] == {"retrieval": "skipped"}

    assert failed_run_response.json()["status"] == "pending"
    assert failed_run.json()["status"] == "failed"
    assert failed_run.json()["metrics"]["failed_cases"] == 1
    assert failed_run.json()["error_summary"] == "1 evaluation case(s) failed"
    assert failed_run.json()["results"][0]["type_statuses"] == {"retrieval": "failed"}
    assert "forced retrieval failure" in failed_run.json()["results"][0]["error_message"]

    assert history.status_code == 200
    assert history.json()["total"] == 3
    assert history.json()["page"] == 1
    assert history.json()["page_size"] == 2
    assert history.json()["items"][0]["name"] == "Failed evaluation"
    assert set(history.json()["items"][0]) >= {
        "created_by",
        "created_at",
        "started_at",
        "finished_at",
        "metrics",
        "error_summary",
        "request_id",
    }
    assert success_history.json()["total"] == 1
    assert success_history.json()["items"][0]["name"] == "Answer evaluation"
    assert creator_history.json()["total"] == 3
