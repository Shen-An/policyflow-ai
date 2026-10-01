"""T071 [US2] LLM concurrency + token call-site wiring (broker-free).

The Stage-4 telemetry instruments for in-flight LLM concurrency and tokens
(``METRIC_LLM_CONCURRENCY`` / ``METRIC_LLM_TOKENS``) have existed since R14, but
R21/R25 recorded an honest gap: they had no call-site, so the gauge never moved
and no tokens were ever counted. These tests pin the wiring at the single HTTP
choke point every LLM call funnels through -- ``OpenAICompatibleLLMService._post_json``:

- the in-flight concurrency up-down counter reads 1 while a call is in flight and
  returns to 0 after it finishes -- including when the provider errors, so a
  ``finally`` must release the slot (a leaked gauge would read load that is gone);
- prompt and completion tokens from the provider's ``usage`` block are counted by
  direction, and a response without ``usage`` counts nothing (no fabricated zero).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.init_db import initialize_database
from backend.app.db.session import build_engine
from backend.app.observability import telemetry as tm
from backend.app.services.llm_service import OpenAICompatibleLLMService

pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'llm-telemetry.db').as_posix()}",
        "LOG_DIR": tmp_path / "logs",
        "LLM_BASE_URL": "https://llm.test/v1",
        "LLM_CHAT_MODEL": "test-model",
        "LLM_API_KEY_ENV": "TEST_LLM_KEY",
        "LLM_MAX_CONCURRENCY": 1,
        "LLM_MAX_ATTEMPTS": 2,
        "LLM_RETRY_BASE_SECONDS": 0.01,
        "LLM_RETRY_MAX_SECONDS": 0.02,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture()
def reader():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    tm.reset_telemetry()
    tm.configure_telemetry(enabled=True, meter=meter)
    try:
        yield reader
    finally:
        tm.reset_telemetry()


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    out: list = []
    if data is None:
        return out
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    out.extend(metric.data.data_points)
    return out


def _token_counts(reader: InMemoryMetricReader) -> dict[str, float]:
    return {
        point.attributes.get("direction"): point.value
        for point in _points(reader, tm.METRIC_LLM_TOKENS)
    }


def _concurrency(reader: InMemoryMetricReader) -> float:
    points = _points(reader, tm.METRIC_LLM_CONCURRENCY)
    return points[0].value if points else 0


@pytest.mark.asyncio
async def test_in_flight_concurrency_tracked_and_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: InMemoryMetricReader
) -> None:
    settings = _settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    observed: dict[str, float] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        # Read the gauge while the call is in flight: it must already be +1.
        observed["in_flight"] = _concurrency(reader)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    answer = await service.complete("system", "question")
    await client.aclose()
    engine.dispose()

    assert answer == "ok"
    assert observed["in_flight"] == 1
    assert _concurrency(reader) == 0


@pytest.mark.asyncio
async def test_tokens_counted_by_direction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: InMemoryMetricReader
) -> None:
    settings = _settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    await service.complete("system", "question")
    await client.aclose()
    engine.dispose()

    assert _token_counts(reader) == {"prompt": 12, "completion": 7}


@pytest.mark.asyncio
async def test_no_usage_counts_no_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: InMemoryMetricReader
) -> None:
    settings = _settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    await service.complete("system", "question")
    await client.aclose()
    engine.dispose()

    assert _token_counts(reader) == {}


@pytest.mark.asyncio
async def test_concurrency_released_on_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: InMemoryMetricReader
) -> None:
    settings = _settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)
    monkeypatch.setenv("TEST_LLM_KEY", "secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAICompatibleLLMService(engine, settings, client)
    with pytest.raises(ApplicationError):
        await service.complete("system", "question")
    await client.aclose()
    engine.dispose()

    # The gauge must drain even when the provider errors out.
    assert _concurrency(reader) == 0
    assert _token_counts(reader) == {}
