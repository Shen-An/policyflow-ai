"""Phase 4 FAQ and evaluation acceptance tests."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from backend.app.core.config import Settings
from backend.app.db.models import FAQDraft, KnowledgeBase, KnowledgeDocument
from backend.app.evals.retrieval_metrics import calculate_retrieval_metrics
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class Phase4LightRAG:
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
        return [
            Evidence(
                knowledge_base_id=str(item["knowledge_base_id"]),
                knowledge_base_name=str(item["knowledge_base_name"]),
                document_id=str(item["document_id"]),
                document_title=str(item["document_title"]),
                document_version=int(item["document_version"]),
                snippet=str(item["snippet"]),
                score=0.9,
                retriever_type="lightrag",
                rank=index,
            )
            for index, item in enumerate(self.documents, start=1)
            if item["knowledge_base_id"] in request.knowledge_base_ids
        ][:limit]


class Phase4LLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "Evaluation answer [1]"


def build_phase4_app(tmp_path: Path, backend: Phase4LightRAG) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'phase4.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(settings, lightrag_adapter=backend, llm_service=Phase4LLM())


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_retrieval_metrics_handle_rank_dedup_and_empty_ground_truth() -> None:
    retrieved = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="x",
            snippet="x",
            retriever_type="lightrag",
            rank=1,
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="b",
            snippet="b",
            retriever_type="lightrag",
            rank=2,
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="b",
            snippet="duplicate",
            retriever_type="lightrag",
            rank=3,
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="KB",
            document_id="a",
            snippet="a",
            retriever_type="lightrag",
            rank=4,
        ),
    ]
    metrics = calculate_retrieval_metrics(retrieved, ["a", "b", "c"], [], [1, 3, 5])
    skipped = calculate_retrieval_metrics(retrieved, [], [], [1, 3])

    assert metrics["hit_at_1"] == 0.0
    assert metrics["hit_at_3"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["recall_at_5"] == 2 / 3
    assert skipped == {"status": "skipped", "reason": "empty_ground_truth"}


def test_faq_approval_indexes_document_and_eval_run_is_reproducible(tmp_path: Path) -> None:
    backend = Phase4LightRAG()
    app = build_phase4_app(tmp_path, backend)

    with TestClient(app) as client:
        admin_headers = login(client)
        kb_response = client.get("/api/knowledge-bases", headers=admin_headers)
        hr_id = next(item["id"] for item in kb_response.json()["items"] if item["code"] == "hr")
        upload_response = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={
                "file": (
                    "leave.txt",
                    b"Annual leave requires manager approval. Submit the request in advance.",
                    "text/plain",
                )
            },
            data={"title": "Leave Policy"},
        )
        source_document_id = upload_response.json()["document_id"]
        faq_response = client.post(
            "/api/faq-drafts",
            headers=admin_headers,
            json={
                "knowledge_base_id": hr_id,
                "source_document_id": source_document_id,
                "count": 2,
            },
        )
        first_faq, second_faq = faq_response.json()["items"]
        approve_response = client.post(
            f"/api/faq-drafts/{first_faq['id']}/approve",
            headers=admin_headers,
        )
        reject_response = client.post(
            f"/api/faq-drafts/{second_faq['id']}/reject",
            headers=admin_headers,
            json={"reason": "Duplicate topic"},
        )

        case_response = client.post(
            "/api/eval/cases",
            headers=admin_headers,
            json={
                "question": "How is annual leave approved?",
                "category": "hr",
                "expected_answer_keywords": ["manager approval"],
                "expected_source_documents": ["Leave Policy"],
                "should_answer": True,
            },
        )
        retrieval_item_response = client.post(
            "/api/eval/retrieval-items",
            headers=admin_headers,
            json={
                "eval_case_id": case_response.json()["id"],
                "query": "How is annual leave approved?",
                "knowledge_base_ids": [hr_id],
                "relevant_document_ids": [source_document_id],
                "relevant_chunk_ids": [],
            },
        )
        run_response = client.post(
            "/api/eval/runs",
            headers=admin_headers,
            json={
                "name": "Phase 4 retrieval eval",
                "case_ids": [case_response.json()["id"]],
                "retrieval_item_ids": [retrieval_item_response.json()["id"]],
                "eval_types": ["retrieval", "ragas"],
                "retrieval_config": {
                    "strategy": "lightrag_only",
                    "top_k_values": [1, 3, 5],
                    "rerank_enabled": False,
                    "query_mode": "hybrid",
                },
                "ragas_config": {"enabled": False, "metrics": ["faithfulness"]},
            },
        )
        run_id = run_response.json()["id"]
        completed_run_response = client.get(
            f"/api/eval/runs/{run_id}",
            headers=admin_headers,
        )
        debug_response = client.post(
            "/api/eval/retrieval-debug",
            headers=admin_headers,
            json={
                "query": "annual leave",
                "knowledge_base_ids": [hr_id],
                "strategy": "lightrag_only",
                "top_k": 5,
                "query_mode": "hybrid",
            },
        )
        bm25_response = client.post(
            "/api/eval/retrieval-debug",
            headers=admin_headers,
            json={
                "query": "annual leave",
                "knowledge_base_ids": [hr_id],
                "strategy": "bm25_only",
            },
        )
        rerank_response = client.post(
            "/api/eval/retrieval-debug",
            headers=admin_headers,
            json={
                "query": "annual leave",
                "knowledge_base_ids": [hr_id],
                "strategy": "lightrag_only",
                "rerank_enabled": True,
            },
        )

    assert faq_response.status_code == 201
    assert approve_response.status_code == 200
    assert approve_response.json()["faq_draft"]["status"] == "approved"
    assert reject_response.json()["status"] == "rejected"
    assert reject_response.json()["review_note"] == "Duplicate topic"
    assert run_response.status_code == 201
    assert run_response.json()["status"] == "pending"
    assert completed_run_response.status_code == 200
    run = completed_run_response.json()
    assert run["status"] == "success"
    assert run["config_snapshot"]["retrieval_config"]["top_k_values"] == [1, 3, 5]
    retrieval_result = next(
        result for result in run["results"] if result["retrieval_metrics"] is not None
    )
    answer_result = next(
        result for result in run["results"] if result["answer_metrics"] is not None
    )
    assert retrieval_result["retrieval_metrics"]["hit_at_1"] == 1.0
    assert retrieval_result["retrieval_metrics"]["mrr"] == 1.0
    assert answer_result["ragas_metrics"] == {
        "status": "skipped",
        "metrics": {},
        "reason": "disabled",
    }
    assert debug_response.status_code == 200
    assert debug_response.json()["items"][0]["retriever_type"] == "lightrag"
    assert debug_response.json()["items"][0]["score"] == 0.9
    assert bm25_response.status_code == 200
    assert bm25_response.json()["items"]
    assert bm25_response.json()["items"][0]["retriever_type"] == "bm25"
    assert bm25_response.json()["items"][0]["chunk_id"] is None
    assert rerank_response.status_code == 503
    assert rerank_response.json()["error"]["code"] == "RERANKER_UNAVAILABLE"

    with Session(app.state.engine) as session:
        approved_faq = session.get(FAQDraft, first_faq["id"])
        faq_document = session.get(KnowledgeDocument, approve_response.json()["document_id"])
    assert approved_faq is not None
    assert faq_document is not None
    assert faq_document.index_status == "indexed"
    assert len(backend.documents) == 2
