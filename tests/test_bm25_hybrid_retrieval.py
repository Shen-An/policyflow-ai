"""Unit tests for document-level BM25 and RRF hybrid retrieval."""

from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.models import KnowledgeBase, KnowledgeDocument
from backend.app.db.session import build_engine
from backend.app.rag.bm25_retriever import BM25Retriever, extract_snippet, tokenize
from backend.app.rag.hybrid_retriever import HybridRetriever
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalStrategy
from backend.app.services.rag_service import RAGService


def test_tokenize_english_and_chinese_bigrams() -> None:
    tokens = tokenize("Hotel 住宿标准 policy")
    assert "hotel" in tokens
    assert "policy" in tokens
    assert "住宿" in tokens
    assert "宿标" in tokens
    assert "标准" in tokens


def test_extract_snippet_caps_length_and_centers_match() -> None:
    text = "前言。" + ("填充" * 80) + "差旅住宿标准不得超过上限。" + ("尾部" * 80)
    snippet = extract_snippet(text, tokenize("住宿标准"), target=80)
    assert "住宿" in snippet
    assert len(snippet) <= 400
    assert snippet.startswith("...") or snippet.endswith("...")


class FixedRankRetriever:
    def __init__(self, name: str, items: list[Evidence], available: bool = True) -> None:
        self.name = name
        self.items = items
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return self.items[:limit]


@pytest.mark.asyncio
async def test_bm25_ranks_and_skips_empty_content(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'bm25.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    with Session(engine) as session:
        knowledge_base = session.exec(select(KnowledgeBase).where(KnowledgeBase.code == "hr")).one()
        session.add(
            KnowledgeDocument(
                knowledge_base_id=knowledge_base.id,
                title="Travel Policy",
                file_path="travel.txt",
                file_type="txt",
                content_text="Hotel reimbursement is capped at 500 per night.",
                content_hash="hash-travel",
                index_status="indexed",
                created_by="admin",
            )
        )
        session.add(
            KnowledgeDocument(
                knowledge_base_id=knowledge_base.id,
                title="Leave Policy",
                file_path="leave.txt",
                file_type="txt",
                content_text="Annual leave requires manager approval.",
                content_hash="hash-leave",
                index_status="indexed",
                created_by="admin",
            )
        )
        session.add(
            KnowledgeDocument(
                knowledge_base_id=knowledge_base.id,
                title="Empty",
                file_path="empty.txt",
                file_type="txt",
                content_text="   ",
                content_hash="hash-empty",
                index_status="indexed",
                created_by="admin",
            )
        )
        session.commit()
        knowledge_base_id = knowledge_base.id

    retriever = BM25Retriever(engine)
    result = await retriever.retrieve(
        RetrievalRequest(
            query="hotel reimbursement",
            knowledge_base_ids=[knowledge_base_id],
            strategy=RetrievalStrategy.BM25_ONLY,
            top_k=5,
        ),
        limit=5,
    )

    assert result
    assert result[0].document_title == "Travel Policy"
    assert result[0].chunk_id is None
    assert result[0].retriever_type == "bm25"
    assert result[0].score is not None
    assert len(result[0].snippet) <= 400
    assert "hotel" in result[0].snippet.lower()


@pytest.mark.asyncio
async def test_bm25_cache_invalidates_on_content_hash_change(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'bm25-cache.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    with Session(engine) as session:
        knowledge_base = session.exec(select(KnowledgeBase).where(KnowledgeBase.code == "hr")).one()
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            title="Policy",
            file_path="policy.txt",
            file_type="txt",
            content_text="Original hotel limit is low.",
            content_hash="hash-v1",
            index_status="indexed",
            created_by="admin",
        )
        session.add(document)
        session.commit()
        knowledge_base_id = knowledge_base.id
        document_id = document.id

    retriever = BM25Retriever(engine)
    first = await retriever.retrieve(
        RetrievalRequest(
            query="hotel",
            knowledge_base_ids=[knowledge_base_id],
            strategy=RetrievalStrategy.BM25_ONLY,
        ),
        limit=5,
    )
    assert first and "Original hotel" in first[0].snippet

    with Session(engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.content_text = "Updated airfare rules with no lodging terms."
        document.content_hash = "hash-v2"
        session.add(document)
        session.commit()

    second = await retriever.retrieve(
        RetrievalRequest(
            query="hotel",
            knowledge_base_ids=[knowledge_base_id],
            strategy=RetrievalStrategy.BM25_ONLY,
        ),
        limit=5,
    )
    assert second == []


@pytest.mark.asyncio
async def test_hybrid_rrf_fusion_and_availability_gate() -> None:
    lightrag = FixedRankRetriever(
        "lightrag",
        [
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-a",
                chunk_id="chunk-1",
                snippet="LightRAG chunk A",
                score=0.8,
                retriever_type="lightrag",
                rank=1,
            ),
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-b",
                chunk_id="chunk-2",
                snippet="LightRAG chunk B",
                score=0.5,
                retriever_type="lightrag",
                rank=2,
            ),
        ],
    )
    bm25 = FixedRankRetriever(
        "bm25",
        [
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-b",
                snippet="BM25 document B",
                score=3.0,
                retriever_type="bm25",
                rank=1,
            ),
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="KB",
                document_id="doc-c",
                snippet="BM25 document C",
                score=1.0,
                retriever_type="bm25",
                rank=2,
            ),
        ],
    )
    hybrid = HybridRetriever(lightrag, bm25, rrf_k=60)
    assert hybrid.available is True

    fused = await hybrid.retrieve(
        RetrievalRequest(
            query="policy",
            knowledge_base_ids=["kb"],
            strategy=RetrievalStrategy.HYBRID_LIGHTRAG_BM25,
            top_k=5,
        ),
        limit=5,
    )
    assert [item.document_id for item in fused] == ["doc-b", "doc-a", "doc-c"]
    assert fused[0].snippet == "LightRAG chunk B"
    assert fused[0].metadata["source_retrievers"] == ["lightrag", "bm25"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].chunk_id is None
    assert fused[2].metadata["source_retrievers"] == ["bm25"]

    unavailable = HybridRetriever(lightrag, FixedRankRetriever("bm25", [], available=False))
    assert unavailable.available is False
    with pytest.raises(ApplicationError) as error:
        await unavailable.retrieve(
            RetrievalRequest(
                query="policy",
                knowledge_base_ids=["kb"],
                strategy=RetrievalStrategy.HYBRID_LIGHTRAG_BM25,
            ),
            limit=5,
        )
    assert error.value.code == "RETRIEVAL_STRATEGY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_unwired_bm25_placeholder_still_unavailable() -> None:
    service = RAGService(FixedRankRetriever("lightrag", []))
    with pytest.raises(ApplicationError) as error:
        await service.retrieve(
            RetrievalRequest(
                query="policy",
                knowledge_base_ids=["kb"],
                strategy=RetrievalStrategy.BM25_ONLY,
            )
        )
    assert error.value.code == "RETRIEVAL_STRATEGY_UNAVAILABLE"
