"""Phase 5 request tracing, error boundaries, and final acceptance tests."""

import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.models import AuditLog, KnowledgeBase
from backend.app.db.session import build_engine
from backend.app.main import create_app
from backend.app.rag.lightrag_adapter import LightRAGAdapter
from backend.app.schemas.retrieval import Evidence, RetrievalRequest
from backend.app.services.rag_service import RAGService


class NoEvidenceRetriever:
    name = "lightrag"

    @property
    def available(self) -> bool:
        return True

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return []


class FailingLLM:
    @property
    def available(self) -> bool:
        return False

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("LLM must not run without evidence")


class RankedRetriever:
    name = "lightrag"

    @property
    def available(self) -> bool:
        return True

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return [
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-a",
                snippet="A",
                score=0.4,
                retriever_type=self.name,
                rank=1,
            ),
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-a",
                snippet="A",
                score=0.4,
                retriever_type=self.name,
                rank=2,
            ),
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-b",
                snippet="B",
                score=0.9,
                retriever_type=self.name,
                rank=3,
            ),
        ]


class ReverseReranker:
    @property
    def available(self) -> bool:
        return True

    async def rerank(
        self,
        query: str,
        candidates: Sequence[Evidence],
        limit: int,
    ) -> list[Evidence]:
        return list(reversed(candidates))[:limit]


def build_phase5_settings(tmp_path: Path) -> Settings:
    return Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'phase5.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="phase5-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_request_id_links_headers_errors_logs_and_rejects_unsafe_input(
    tmp_path: Path,
) -> None:
    settings = build_phase5_settings(tmp_path)
    app = create_app(settings)

    @app.get("/phase5/application-error")
    async def application_error() -> None:
        raise ApplicationError("PHASE5_CONFLICT", "Expected conflict", 409)

    @app.get("/phase5/validation/{item_id}")
    async def validation_error(item_id: UUID) -> dict[str, str]:
        return {"id": str(item_id)}

    @app.get("/phase5/unexpected")
    async def unexpected_error() -> None:
        raise RuntimeError("internal password=must-not-leak")

    request_id = "phase5.trace:request-001"
    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health", headers={"X-Request-ID": request_id})
        conflict = client.get(
            "/phase5/application-error",
            headers={"X-Request-ID": request_id},
        )
        validation = client.get(
            "/phase5/validation/not-a-uuid",
            headers={"X-Request-ID": request_id},
        )
        missing = client.get(
            "/phase5/missing",
            headers={"X-Request-ID": request_id},
        )
        unexpected = client.get(
            "/phase5/unexpected",
            headers={"X-Request-ID": request_id},
        )
        unsafe = client.get(
            "/health",
            headers={"X-Request-ID": "unsafe request id"},
        )

    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == request_id
    assert float(health.headers["X-Process-Time-Ms"]) < 15_000

    for response, status_code, error_code in (
        (conflict, 409, "PHASE5_CONFLICT"),
        (validation, 422, "VALIDATION_ERROR"),
        (missing, 404, "HTTP_ERROR"),
        (unexpected, 500, "INTERNAL_ERROR"),
    ):
        assert response.status_code == status_code
        assert response.headers["X-Request-ID"] == request_id
        assert response.json()["success"] is False
        assert response.json()["request_id"] == request_id
        assert response.json()["error"]["code"] == error_code
        assert float(response.headers["X-Process-Time-Ms"]) < 15_000

    assert "must-not-leak" not in unexpected.text
    assert unsafe.headers["X-Request-ID"] != "unsafe request id"
    UUID(unsafe.headers["X-Request-ID"])

    payloads = [
        json.loads(line)
        for line in settings.log_file.read_text(encoding="utf-8").splitlines()
    ]
    traced_payloads = [item for item in payloads if item.get("request_id") == request_id]
    assert any(
        item["message"] == "HTTP request completed"
        and item["request_path"] == "/health"
        and item["status_code"] == 200
        and item["duration_ms"] < 15_000
        for item in traced_payloads
    )
    assert any(
        item["message"] == "Application request failed"
        and item["error_code"] == "PHASE5_CONFLICT"
        for item in traced_payloads
    )
    assert any(
        item["message"] == "Unhandled application exception"
        and item["error_code"] == "INTERNAL_ERROR"
        for item in traced_payloads
    )


def test_audit_records_inherit_request_context_without_manual_plumbing(tmp_path: Path) -> None:
    settings = build_phase5_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        admin_headers = login(client)
        departments = client.get("/api/departments", headers=admin_headers).json()["items"]
        hr_department = next(item for item in departments if item["code"] == "hr")
        create_request_id = "phase5-kb-create"
        create_response = client.post(
            "/api/knowledge-bases",
            headers={**admin_headers, "X-Request-ID": create_request_id},
            json={
                "name": "Phase 5 Trace KB",
                "code": "phase5-trace",
                "department_id": hr_department["id"],
                "description": "Request tracing acceptance",
                "default_query_mode": "hybrid",
            },
        )
        knowledge_base_id = create_response.json()["id"]
        upload_request_id = "phase5-document-upload"
        upload_response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            headers={**admin_headers, "X-Request-ID": upload_request_id},
            files={"file": ("trace.txt", b"Traceable policy.", "text/plain")},
            data={"title": "Trace Policy"},
        )

    assert create_response.status_code == 201
    assert upload_response.status_code == 201
    with Session(app.state.engine) as session:
        audit_logs = session.exec(
            select(AuditLog).where(
                AuditLog.action.in_(["knowledge_base.create", "document.upload"])
            )
        ).all()

    request_ids = {item.action: item.request_id for item in audit_logs}
    assert request_ids["knowledge_base.create"] == create_request_id
    assert request_ids["document.upload"] == upload_request_id


@pytest.mark.asyncio
async def test_rag_service_deduplicates_reranks_and_reassigns_final_ranks() -> None:
    service = RAGService(RankedRetriever(), reranker=ReverseReranker())
    result = await service.retrieve(
        RetrievalRequest(
            query="policy",
            knowledge_base_ids=["kb"],
            top_k=2,
            rerank_enabled=True,
        )
    )

    assert result.rerank_applied is True
    assert [item.document_id for item in result.evidence] == ["doc-b", "doc-a"]
    assert [item.rank for item in result.evidence] == [1, 2]
    assert [item.rank for item in result.trace] == [1, 2]


@pytest.mark.asyncio
async def test_agent_pipeline_marks_no_evidence_answers_as_untrusted() -> None:
    rag_service = RAGService(NoEvidenceRetriever())
    pipeline = AgentPipeline(
        RouterAgent(),
        RetrievalAgent(rag_service),
        AnswerAgent(FailingLLM()),
        SkillAgent(),
        ComplianceAgent(),
    )
    knowledge_base = KnowledgeBase(
        id="kb",
        name="HR",
        code="hr",
        department_id="department",
        rag_workspace="rag/hr",
    )
    result = await pipeline.run(
        "Unknown policy?",
        [knowledge_base],
        RetrievalRequest(query="Unknown policy?", knowledge_base_ids=["kb"]),
    )

    assert result.retrieval_result.evidence == []
    assert result.answer_result.confidence_score == 0.0
    assert result.compliance.warnings == ["NO_RELIABLE_EVIDENCE"]
    assert "不可信" in result.answer_result.answer or "未检索到" in result.answer_result.answer


@pytest.mark.asyncio
async def test_lightrag_upstream_failures_are_sanitized_to_stable_boundary(
    tmp_path: Path,
) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'lightrag-boundary.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        LIGHTRAG_BASE_URL="https://lightrag.test/{workspace}",
        LIGHTRAG_API_KEY="upstream-secret",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    with Session(engine) as session:
        knowledge_base = session.exec(
            select(KnowledgeBase).where(KnowledgeBase.code == "hr")
        ).one()
        knowledge_base_id = knowledge_base.id

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers[settings.LIGHTRAG_API_KEY_HEADER] == "upstream-secret"
        return httpx.Response(503, text="provider leaked secret details")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LightRAGAdapter(engine, settings, client)
    with pytest.raises(ApplicationError) as error:
        await adapter.retrieve(
            RetrievalRequest(
                query="annual leave",
                knowledge_base_ids=[knowledge_base_id],
            ),
            5,
        )
    await client.aclose()

    assert error.value.code == "LIGHTRAG_ERROR"
    assert error.value.status_code == 502
    assert error.value.message == "LightRAG request failed"
    assert error.value.details is None
