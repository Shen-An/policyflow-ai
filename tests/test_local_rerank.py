"""Local lexical reranker behavior."""

from __future__ import annotations

import pytest

from backend.app.rag.rerank_service import RerankService
from backend.app.schemas.retrieval import Evidence
from backend.app.services.rag_service import RAGService
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalStrategy


@pytest.mark.asyncio
async def test_local_reranker_promotes_lexical_match() -> None:
    reranker = RerankService()
    candidates = [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="noise",
            document_title="食堂菜单",
            snippet="今日供应红烧肉与米饭",
            score=0.99,
            retriever_type="lightrag",
            rank=1,
            metadata={"score_is_synthetic": True},
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="travel",
            document_title="差旅制度",
            snippet="差旅住宿标准 500 元，需提前申请",
            score=0.1,
            retriever_type="bm25",
            rank=2,
        ),
    ]
    reranked = await reranker.rerank("差旅住宿标准", candidates, limit=2)
    assert [item.document_id for item in reranked] == ["travel", "noise"]
    assert reranked[0].metadata["rerank_method"] == "local_lexical_fusion"
    assert reranked[0].rerank_score is not None
    assert reranked[0].rerank_score >= reranked[1].rerank_score


class _StubRetriever:
    name = "stub"

    @property
    def available(self) -> bool:
        return True

    async def retrieve(self, request, limit):  # type: ignore[no-untyped-def]
        return [
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="HR",
                document_id="a",
                document_title="A",
                snippet="无关内容",
                score=0.9,
                retriever_type="stub",
                rank=1,
            ),
            Evidence(
                knowledge_base_id="kb",
                knowledge_base_name="HR",
                document_id="b",
                document_title="请假制度",
                snippet="年假需要提前申请并经主管审批",
                score=0.2,
                retriever_type="stub",
                rank=2,
            ),
        ][:limit]


@pytest.mark.asyncio
async def test_rag_service_local_rerank_is_available() -> None:
    service = RAGService(_StubRetriever())  # type: ignore[arg-type]
    result = await service.retrieve(
        RetrievalRequest(
            query="年假申请审批",
            knowledge_base_ids=["kb"],
            strategy=RetrievalStrategy.LIGHTRAG_ONLY,
            top_k=2,
            rerank_enabled=True,
        )
    )
    assert result.rerank_applied is True
    assert result.evidence[0].document_id == "b"
