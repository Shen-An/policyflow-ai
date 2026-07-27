"""Multi-KB LightRAG retrieve concurrency cap tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.init_db import initialize_database
from backend.app.db.models import KnowledgeBase
from backend.app.db.session import build_engine
from backend.app.rag.inprocess_lightrag import InProcessLightRAGAdapter
from backend.app.schemas.retrieval import Evidence, LightRAGQueryMode, RetrievalRequest


class _AlwaysAvailable:
    @property
    def available(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_multi_kb_retrieve_respects_concurrency_cap(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'kb-conc.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        LIGHTRAG_RETRIEVE_MAX_CONCURRENCY=1,
        LLM_BASE_URL=None,
        LLM_CHAT_MODEL=None,
        LLM_EMBEDDING_MODEL=None,
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)

    adapter = InProcessLightRAGAdapter(
        engine,
        settings,
        _AlwaysAvailable(),  # type: ignore[arg-type]
        _AlwaysAvailable(),  # type: ignore[arg-type]
        retrieve_max_concurrency=1,
    )

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()
    kb_ids: list[str] = []

    async def fake_retrieve_workspace(
        knowledge_base: KnowledgeBase, request: RetrievalRequest, limit: int
    ) -> list[Evidence]:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            kb_ids.append(knowledge_base.id)
        await asyncio.sleep(0.04)
        async with lock:
            in_flight -= 1
        return [
            Evidence(
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                document_id=None,
                document_title=None,
                document_version=None,
                chunk_id=f"{knowledge_base.id}-chunk",
                source_id=None,
                snippet=f"evidence from {knowledge_base.code}",
                score=0.9,
                retriever_type="lightrag",
                rank=1,
                metadata={},
            )
        ]

    adapter._retrieve_workspace = fake_retrieve_workspace  # type: ignore[method-assign]

    with Session(engine) as session:
        bases = list(session.exec(select(KnowledgeBase)).all())
    assert len(bases) >= 3
    selected = bases[:3]

    evidence = await adapter.retrieve(
        RetrievalRequest(
            query="leave policy",
            knowledge_base_ids=[item.id for item in selected],
            lightrag_query_mode=LightRAGQueryMode.HYBRID,
        ),
        limit=10,
    )
    engine.dispose()

    assert len(evidence) == 3
    assert max_in_flight == 1
    assert set(kb_ids) == {item.id for item in selected}
