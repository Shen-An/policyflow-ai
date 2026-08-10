"""Reranker selection is controlled by the retrieval request."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalStrategy
from backend.app.services.rag_service import RAGService


class _StubRetriever:
    name = "stub"

    @property
    def available(self) -> bool:
        return True

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return [
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="HR",
                document_id="a",
                document_title="A",
                snippet="first",
                score=0.9,
                retriever_type="stub",
                rank=1,
            ),
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="HR",
                document_id="b",
                document_title="B",
                snippet="second",
                score=0.1,
                retriever_type="stub",
                rank=2,
            ),
        ][:limit]


class _StubReranker:
    def __init__(self, method: str) -> None:
        self.method = method
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    async def rerank(self, query: str, candidates: list[Evidence], limit: int) -> list[Evidence]:
        self.calls += 1
        return [
            item.model_copy(
                update={
                    "rerank_score": 1.0,
                    "metadata": {"rerank_method": self.method},
                }
            )
            for item in reversed(candidates[:limit])
        ]


@pytest.mark.asyncio
async def test_retrieval_request_selects_cross_encoder_reranker() -> None:
    local = _StubReranker("local_lexical_fusion")
    cross_encoder = _StubReranker("cross_encoder")
    service = RAGService(
        _StubRetriever(),  # type: ignore[arg-type]
        rerankers={
            "local_lexical_fusion": local,  # type: ignore[dict-item]
            "cross_encoder": cross_encoder,  # type: ignore[dict-item]
        },
    )

    result = await service.retrieve(
        RetrievalRequest(
            query="policy",
            knowledge_base_ids=["kb"],
            strategy=RetrievalStrategy.LIGHTRAG_ONLY,
            top_k=1,
            candidate_k=2,
            rerank_enabled=True,
            reranker_method="cross_encoder",
        )
    )

    assert cross_encoder.calls == 1
    assert local.calls == 0
    assert result.evidence[0].metadata["rerank_method"] == "cross_encoder"


def test_reranker_status_exposes_page_selectable_choices(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    app = create_app(
        Settings(
            DATABASE_URL=f"sqlite:///{(tmp_path / 'reranker-status.db').as_posix()}",
            LOG_DIR=tmp_path / "logs",
            SECRET_KEY="reranker-status-secret",
            BOOTSTRAP_ADMIN_PASSWORD="test-password",
            _env_file=None,
        )
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        response = client.get(
            "/api/eval/reranker-status",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["method"] == "local_lexical_fusion"
    assert [option["method"] for option in payload["options"]] == [
        "local_lexical_fusion",
        "cross_encoder",
    ]
    assert payload["options"][0]["available"] is True
    assert payload["options"][1]["available"] is False
    assert payload["options"][1]["models"] == [
        "nvidia/llama-nemotron-rerank-vl-1b-v2",
        "nvidia/llama-nemotron-rerank-1b-v2",
        "nvidia/rerank-qa-mistral-4b",
    ]


def test_legacy_local_reranker_is_not_misreported_as_cross_encoder() -> None:
    service = RAGService(
        _StubRetriever(),  # type: ignore[arg-type]
        reranker=_StubReranker("local_lexical_fusion"),  # type: ignore[arg-type]
    )

    with pytest.raises(ApplicationError, match="Unknown reranker method"):
        service.validate_configuration(
            RetrievalStrategy.LIGHTRAG_ONLY,
            rerank_enabled=True,
            reranker_method="cross_encoder",
        )
