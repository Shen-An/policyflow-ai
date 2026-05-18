"""Unit tests for embedding provider retry and error messaging."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.models import ModelProvider
from backend.app.db.session import build_engine
from backend.app.services.embedding_service import OpenAICompatibleEmbeddingService


def _build_engine(tmp_path: Path):
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'embedding-retry.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY="embedding-retry-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    return engine, settings


def _seed_embedding_provider(engine, *, base_url: str = "https://embed.example.com/v1") -> None:
    with Session(engine) as session:
        # Replace any bootstrap embedding provider so the test controls the endpoint.
        existing = session.exec(
            # type: ignore[attr-defined]
            __import__("sqlmodel", fromlist=["select"]).select(ModelProvider).where(
                ModelProvider.capability == "embedding"
            )
        ).all()
        for item in existing:
            session.delete(item)
        session.add(
            ModelProvider(
                name="test-embedding",
                provider_type="openai_compatible",
                capability="embedding",
                base_url=base_url,
                api_key_env="OPENAI_API_KEY",
                default_chat_model="unused-chat",
                default_embedding_model="embed-test",
                enabled=True,
                config_json={
                    "auth_mode": "none",
                    "timeout_seconds": 5.0,
                    "api_style": "openai_embeddings",
                    "embedding_input_type": "none",
                },
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_embed_retries_connect_error_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    engine, settings = _build_engine(tmp_path)
    _seed_embedding_provider(engine)

    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.ConnectError("simulated connect failure", request=request)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleEmbeddingService(
        engine,
        settings,
        client=client,
        max_attempts=3,
        retry_base_seconds=0.01,
    )
    try:
        vectors = await service.embed(["hello", "world"])
    finally:
        await client.aclose()
        engine.dispose()

    assert calls["count"] == 3
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.asyncio
async def test_embed_connect_error_message_is_explicit_after_retries(
    tmp_path: Path, monkeypatch
) -> None:
    engine, settings = _build_engine(tmp_path)
    _seed_embedding_provider(engine)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connect failure", request=request)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleEmbeddingService(
        engine,
        settings,
        client=client,
        max_attempts=3,
        retry_base_seconds=0.01,
    )
    try:
        with pytest.raises(ApplicationError) as exc_info:
            await service.embed(["hello"])
    finally:
        await client.aclose()
        engine.dispose()

    error = exc_info.value
    assert error.code == "EMBEDDING_PROVIDER_ERROR"
    assert "无法连接 Embedding 服务" in error.message
    assert "已重试 3 次仍失败" in error.message


@pytest.mark.asyncio
async def test_embed_retries_retryable_http_status(tmp_path: Path, monkeypatch) -> None:
    engine, settings = _build_engine(tmp_path)
    _seed_embedding_provider(engine)

    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, json={"error": {"message": "temporary unavailable"}})
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0]}]})

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleEmbeddingService(
        engine,
        settings,
        client=client,
        max_attempts=3,
        retry_base_seconds=0.01,
    )
    try:
        vectors = await service.embed(["only-one"])
    finally:
        await client.aclose()
        engine.dispose()

    assert calls["count"] == 2
    assert vectors == [[1.0, 2.0]]
