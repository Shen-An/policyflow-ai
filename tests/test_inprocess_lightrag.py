"""Official in-process LightRAG integration tests."""

from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.init_db import initialize_database
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, ModelProvider
from backend.app.db.session import build_engine
from backend.app.rag.inprocess_lightrag import InProcessLightRAGAdapter
from backend.app.schemas.retrieval import LightRAGQueryMode, RetrievalRequest


class FakeGraphLLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "<|COMPLETE|>"


class FakeEmbeddingService:
    @property
    def available(self) -> bool:
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.5, 0.25, 0.125, 0.1, 0.05, 0.025, 0.01] for _ in texts]


@pytest.mark.asyncio
async def test_official_lightrag_indexes_and_queries_document(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'lightrag.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        LLM_BASE_URL=None,
        LLM_CHAT_MODEL=None,
        LLM_EMBEDDING_MODEL=None,
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    with Session(engine) as session:
        session.exec(delete(ModelProvider))
        session.add(
            ModelProvider(
                name="chat-test",
                capability="chat",
                base_url="http://test",
                api_key_env="TEST_KEY",
                default_chat_model="fake-chat",
            )
        )
        session.add(
            ModelProvider(
                name="embedding-test",
                capability="embedding",
                base_url="http://test",
                api_key_env="TEST_KEY",
                default_chat_model="fake-embedding",
                default_embedding_model="fake-embedding",
                config_json={"embedding_dim": 8},
            )
        )
        session.commit()
        knowledge_base = session.exec(select(KnowledgeBase).where(KnowledgeBase.code == "hr")).one()
        knowledge_base.rag_workspace = str(tmp_path / "rag" / "hr")
        session.add(knowledge_base)
        session.commit()
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            title="Leave Policy",
            file_path="leave.txt",
            file_type="txt",
            content_text="Annual leave requires manager approval.",
            content_hash="lightrag-test",
            index_status="pending",
            created_by="system",
        )
        session.add(document)
        session.commit()
        session.refresh(knowledge_base)
        session.refresh(document)
        empty_knowledge_base = session.exec(
            select(KnowledgeBase).where(KnowledgeBase.code == "legal")
        ).one()
        empty_knowledge_base.rag_workspace = str(tmp_path / "rag" / "legal")
        session.add(empty_knowledge_base)
        session.commit()
        session.refresh(empty_knowledge_base)
        session.refresh(knowledge_base)
        session.refresh(document)
        detached_empty_kb = KnowledgeBase.model_validate(empty_knowledge_base.model_dump())
        detached_kb = KnowledgeBase.model_validate(knowledge_base.model_dump())
        detached_document = KnowledgeDocument.model_validate(document.model_dump())

    adapter = InProcessLightRAGAdapter(
        engine,
        settings,
        FakeGraphLLM(),
        FakeEmbeddingService(),
    )
    await adapter.insert_document(detached_kb, detached_document)
    evidence = await adapter.retrieve(
        RetrievalRequest(
            query="annual leave",
            knowledge_base_ids=[detached_kb.id],
            lightrag_query_mode=LightRAGQueryMode.NAIVE,
        ),
        5,
    )
    empty_evidence = await adapter.retrieve(
        RetrievalRequest(
            query="unavailable policy",
            knowledge_base_ids=[detached_empty_kb.id],
            lightrag_query_mode=LightRAGQueryMode.NAIVE,
        ),
        5,
    )
    await adapter.close()
    engine.dispose()

    assert empty_evidence == []
    assert evidence
    assert evidence[0].document_id == detached_document.id
    assert evidence[0].retriever_type == "lightrag"
    assert evidence[0].snippet == "Annual leave requires manager approval."
    assert (tmp_path / "rag" / "hr" / "graph_chunk_entity_relation.graphml").exists()
