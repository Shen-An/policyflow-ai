"""NVIDIA Cross-Encoder reranker fallback behavior."""

from __future__ import annotations

from json import loads

import httpx
import pytest

from backend.app.core.exceptions import ApplicationError
from backend.app.rag.cross_encoder_rerank_service import NvidiaCrossEncoderRerankService
from backend.app.schemas.retrieval import Evidence


def _candidates() -> list[Evidence]:
    return [
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="noise",
            document_title="体检安排",
            snippet="年度体检在六月进行。",
            score=0.9,
            retriever_type="hybrid",
            rank=1,
        ),
        Evidence(
            knowledge_base_id="kb",
            knowledge_base_name="HR",
            document_id="travel",
            document_title="差旅制度",
            snippet="差旅住宿标准为 500 元每晚。",
            score=0.2,
            retriever_type="hybrid",
            rank=2,
        ),
    ]


@pytest.mark.asyncio
async def test_nvidia_reranker_falls_back_to_next_model() -> None:
    seen_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = loads(request.content)
        model = str(payload["model"])
        seen_models.append(model)
        if model == "nvidia/first":
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "rankings": [
                    {"index": 1, "logit": 0.95},
                    {"index": 0, "logit": 0.10},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NvidiaCrossEncoderRerankService(
        models=("nvidia/first", "nvidia/second", "nvidia/third"),
        base_url="https://example.test",
        api_key="test-key",
        client=client,
    )

    reranked = await service.rerank("差旅住宿标准", _candidates(), limit=2)

    assert seen_models == ["nvidia/first", "nvidia/second"]
    assert [item.document_id for item in reranked] == ["travel", "noise"]
    assert reranked[0].metadata["rerank_method"] == "cross_encoder"
    assert reranked[0].metadata["rerank_provider"] == "nvidia"
    assert reranked[0].metadata["rerank_model"] == "nvidia/second"
    await client.aclose()


@pytest.mark.asyncio
async def test_nvidia_reranker_rotates_successful_start_model() -> None:
    seen_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model = str(loads(request.content)["model"])
        seen_models.append(model)
        return httpx.Response(
            200,
            json={
                "rankings": [
                    {"index": 0, "logit": 0.9},
                    {"index": 1, "logit": 0.1},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NvidiaCrossEncoderRerankService(
        models=("nvidia/first", "nvidia/second", "nvidia/third"),
        base_url="https://example.test",
        api_key="test-key",
        client=client,
    )

    await service.rerank("query", _candidates(), limit=1)
    await service.rerank("query", _candidates(), limit=1)

    assert seen_models == ["nvidia/first", "nvidia/second"]
    await client.aclose()


@pytest.mark.asyncio
async def test_nvidia_reranker_reports_all_models_failed() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = NvidiaCrossEncoderRerankService(
        models=("nvidia/first", "nvidia/second"),
        base_url="https://example.test",
        api_key="test-key",
        client=client,
    )

    with pytest.raises(ApplicationError, match="All configured NVIDIA"):
        await service.rerank("query", _candidates(), limit=1)
    await client.aclose()
