"""Phase 2 retrieval, indexing, Agent Pipeline, and chat tests."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.models import (
    AIQueryLog,
    Department,
    KnowledgeBase,
    KnowledgeDocument,
)
from backend.app.db.session import build_engine
from backend.app.main import create_app
from backend.app.rag.inprocess_lightrag import InProcessLightRAGAdapter
from backend.app.rag.lightrag_adapter import LightRAGAdapter
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalStrategy
from backend.app.services.llm_service import OpenAICompatibleLLMService
from backend.app.services.rag_service import RAGService


class FakeLightRAGBackend:
    name = "lightrag"

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.last_request: RetrievalRequest | None = None

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
        self.last_request = request
        if "unknown" in request.query.lower():
            return []
        evidence = []
        for item in self.documents:
            if item["knowledge_base_id"] in request.knowledge_base_ids:
                evidence.append(
                    Evidence(
                        **item,
                        chunk_id="chunk-1",
                        source_id="fake-source",
                        score=0.9,
                        retriever_type="lightrag",
                        rank=len(evidence) + 1,
                    )
                )
        return evidence[:limit]


class FakeLLMService:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "根据测试制度，住宿标准按公司规定执行 [1]。"


def build_phase2_app(tmp_path: Path, backend: FakeLightRAGBackend) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'phase2-test.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag-workspaces",
        SECRET_KEY="test-secret-key",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(
        settings,
        lightrag_adapter=backend,
        llm_service=FakeLLMService(),
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


def create_hr_employee(client: TestClient, app: FastAPI, admin_headers: dict[str, str]) -> str:
    with Session(app.state.engine) as session:
        department = session.exec(select(Department).where(Department.code == "hr")).one()
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "ragemployee",
            "email": "ragemployee@example.com",
            "display_name": "RAG Employee",
            "password": "employee-password",
            "department_id": department.id,
            "role_codes": ["employee"],
        },
    )
    assert response.status_code == 201
    return login(client, "ragemployee", "employee-password")


@pytest.mark.asyncio
async def test_rag_service_deduplicates_and_rejects_unavailable_modes() -> None:
    backend = FakeLightRAGBackend()
    backend.documents = [
        {
            "knowledge_base_id": "kb-1",
            "knowledge_base_name": "Finance",
            "document_id": "doc-1",
            "document_title": "Travel Policy",
            "document_version": 1,
            "snippet": "Hotel limit is defined.",
        },
        {
            "knowledge_base_id": "kb-1",
            "knowledge_base_name": "Finance",
            "document_id": "doc-1",
            "document_title": "Travel Policy",
            "document_version": 1,
            "snippet": "Hotel limit is defined.",
        },
    ]
    service = RAGService(backend)
    result = await service.retrieve(
        RetrievalRequest(
            query="hotel",
            knowledge_base_ids=["kb-1"],
            top_k=5,
            strategy=RetrievalStrategy.LIGHTRAG_ONLY,
        )
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].rank == 1
    assert result.trace[0].retrieval_strategy == RetrievalStrategy.LIGHTRAG_ONLY

    with pytest.raises(ApplicationError) as bm25_error:
        await service.retrieve(
            RetrievalRequest(
                query="hotel",
                knowledge_base_ids=["kb-1"],
                strategy=RetrievalStrategy.BM25_ONLY,
            )
        )
    assert bm25_error.value.code == "RETRIEVAL_STRATEGY_UNAVAILABLE"

    with pytest.raises(ApplicationError) as hybrid_error:
        await service.retrieve(
            RetrievalRequest(
                query="hotel",
                knowledge_base_ids=["kb-1"],
                strategy=RetrievalStrategy.HYBRID_LIGHTRAG_BM25,
            )
        )
    assert hybrid_error.value.code == "RETRIEVAL_STRATEGY_UNAVAILABLE"

    reranked = await service.retrieve(
        RetrievalRequest(
            query="hotel",
            knowledge_base_ids=["kb-1"],
            strategy=RetrievalStrategy.LIGHTRAG_ONLY,
            rerank_enabled=True,
        )
    )
    assert reranked.rerank_applied is True
    assert reranked.evidence
    assert reranked.evidence[0].metadata.get("rerank_method") == "local_lexical_fusion"


@pytest.mark.asyncio
async def test_lightrag_adapter_maps_references_to_documents(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'adapter.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        LIGHTRAG_BASE_URL="https://lightrag.test/{workspace}",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    with Session(engine) as session:
        knowledge_base = session.exec(select(KnowledgeBase).where(KnowledgeBase.code == "hr")).one()
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            title="HR Policy",
            file_path="uploads/hr/policy.txt",
            file_type="txt",
            content_text="Annual leave is defined.",
            content_hash="hash",
            index_status="indexed",
            created_by="system",
        )
        session.add(document)
        session.commit()
        session.refresh(knowledge_base)
        session.refresh(document)
        knowledge_base = KnowledgeBase.model_validate(knowledge_base.model_dump())
        document = KnowledgeDocument.model_validate(document.model_dump())

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents/text"):
            return httpx.Response(200, json={"status": "success"})
        payload = json.loads(request.content)
        assert payload["only_need_context"] is True
        return httpx.Response(
            200,
            json={
                "response": "Annual leave is defined.",
                "references": [
                    {
                        "reference_id": "ref-1",
                        "file_path": f"policyflow/hr/{document.id}/policy.txt",
                        "content": "Annual leave is defined.",
                        "score": 0.88,
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = LightRAGAdapter(engine, settings, client)
    await adapter.insert_document(knowledge_base, document)
    result = await adapter.retrieve(
        RetrievalRequest(query="annual leave", knowledge_base_ids=[knowledge_base.id]),
        5,
    )
    await client.aclose()

    assert result[0].document_id == document.id
    assert result[0].document_title == "HR Policy"
    assert result[0].score == 0.88
    assert result[0].snippet == "Annual leave is defined."


@pytest.mark.asyncio
async def test_openai_compatible_llm_service_uses_seeded_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'llm.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        LLM_BASE_URL="https://llm.test/v1",
        LLM_CHAT_MODEL="test-model",
        LLM_API_KEY_ENV="TEST_LLM_KEY",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Grounded answer [1]"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    answer = await service.complete("system", "question")
    await client.aclose()

    assert answer == "Grounded answer [1]"


def test_chat_returns_citations_refuses_without_evidence_and_logs_trace(tmp_path: Path) -> None:
    backend = FakeLightRAGBackend()
    app = build_phase2_app(tmp_path, backend)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        employee_headers = headers(create_hr_employee(client, app, admin_headers))
        employee_kbs = client.get("/api/knowledge-bases", headers=employee_headers).json()
        hr_id = employee_kbs["items"][0]["id"]
        upload_response = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("travel.txt", b"Hotel limit is 500 yuan.", "text/plain")},
            data={"title": "Travel Policy"},
        )
        chat_response = client.post(
            "/api/chat",
            headers=employee_headers,
            json={"question": "What is the hotel limit?"},
        )
        refusal_response = client.post(
            "/api/chat",
            headers=employee_headers,
            json={
                "question": "unknown zzzz unmatched question",
                "retrieval_strategy": "lightrag_only",
            },
        )
        conversation_response = client.get(
            f"/api/conversations/{chat_response.json()['conversation_id']}",
            headers=employee_headers,
        )

    assert upload_response.status_code == 201
    assert chat_response.status_code == 200
    assert chat_response.json()["citations"][0]["document_title"] == "Travel Policy"
    assert "[1]" in chat_response.json()["answer"]
    assert backend.last_request is not None
    assert backend.last_request.knowledge_base_ids == [hr_id]
    assert refusal_response.status_code == 200
    assert refusal_response.json()["citations"] == []
    assert refusal_response.json()["confidence_score"] == 0.0
    assert "NO_RELIABLE_EVIDENCE" in refusal_response.json()["compliance"]["warnings"]
    assert refusal_response.json()["compliance"]["passed"] is False
    refusal_answer = refusal_response.json()["answer"]
    assert "没有检索到可靠制度证据" in refusal_answer or "未检索到" in refusal_answer
    assert conversation_response.status_code == 200
    assert len(conversation_response.json()["messages"]) == 2

    with Session(app.state.engine) as session:
        query_logs = session.exec(select(AIQueryLog)).all()
        indexed_document = session.exec(select(KnowledgeDocument)).one()

    assert len(query_logs) == 2
    assert query_logs[0].retrieved_sources[0]["retrieval_strategy"] == "hybrid_lightrag_bm25"
    assert query_logs[0].retrieved_sources[0]["lightrag_query_mode"] == "hybrid"
    assert indexed_document.index_status == "indexed"


def test_create_app_defaults_to_inprocess_lightrag(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'embedded-default.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    app = create_app(settings, llm_service=FakeLLMService())

    assert isinstance(app.state.lightrag_adapter, InProcessLightRAGAdapter)
