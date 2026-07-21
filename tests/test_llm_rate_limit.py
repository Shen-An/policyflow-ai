"""LLM concurrency gate and 429 backoff tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.session import build_engine
from backend.app.services.llm_service import OpenAICompatibleLLMService


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'llm-rate.db').as_posix()}",
        "LOG_DIR": tmp_path / "logs",
        "LLM_BASE_URL": "https://llm.test/v1",
        "LLM_CHAT_MODEL": "test-model",
        "LLM_API_KEY_ENV": "TEST_LLM_KEY",
        "LLM_MAX_CONCURRENCY": 1,
        "LLM_MAX_ATTEMPTS": 4,
        "LLM_RETRY_BASE_SECONDS": 0.01,
        "LLM_RETRY_MAX_SECONDS": 0.05,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_llm_retries_429_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    attempts = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "rate"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok after retry"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    answer = await service.complete("system", "question")
    await client.aclose()
    engine.dispose()

    assert answer == "ok after retry"
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_llm_concurrency_serializes_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, LLM_MAX_CONCURRENCY=1)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.03)
        async with lock:
            in_flight -= 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "serial"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    answers = await asyncio.gather(
        service.complete("system", "q1"),
        service.complete("system", "q2"),
        service.complete("system", "q3"),
    )
    await client.aclose()
    engine.dispose()

    assert answers == ["serial", "serial", "serial"]
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_llm_429_exhaustion_raises_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, LLM_MAX_ATTEMPTS=2)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    with pytest.raises(ApplicationError) as exc_info:
        await service.complete("system", "question")
    await client.aclose()
    engine.dispose()

    assert exc_info.value.code == "LLM_PROVIDER_ERROR"
    assert "429" in exc_info.value.message
