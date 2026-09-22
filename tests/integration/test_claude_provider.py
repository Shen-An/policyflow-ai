"""Provider contract: typed results, classified errors, bounded retry (T050)."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.integrations.claude_provider import ClaudeProvider, ClaudeProviderError


def _settings(**overrides) -> Settings:
    base = dict(
        CLAUDE_STREAMING_ENABLED=False,
        CLAUDE_MAX_RETRIES=2,
        ANTHROPIC_API_KEY=None,
    )
    base.update(overrides)
    return Settings(**base)


def _message(text: str, *, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )


class _Messages:
    def __init__(self, behaviors: list) -> None:
        self._behaviors = behaviors
        self.calls = 0

    async def create(self, **kwargs):
        behavior = self._behaviors[self.calls]
        self.calls += 1
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


class _Client:
    def __init__(self, behaviors: list) -> None:
        self.messages = _Messages(behaviors)


def _connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api"))


def _status_error(code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api")
    response = httpx.Response(code, request=request)
    return anthropic.BadRequestError("bad request", response=response, body=None)


@pytest.mark.asyncio
async def test_completion_reads_text_and_stop_reason() -> None:
    client = _Client([_message("Travel is reimbursable.", stop_reason="end_turn")])
    provider = ClaudeProvider(settings=_settings(), client=client)

    result = await provider.complete(system="s", messages=[{"role": "user", "content": "q"}])

    assert result.text == "Travel is reimbursable."
    assert result.stop_reason == "end_turn"
    assert result.output_tokens == 7


@pytest.mark.asyncio
async def test_retryable_error_is_retried_then_succeeds() -> None:
    client = _Client([_connection_error(), _message("ok")])
    provider = ClaudeProvider(settings=_settings(), client=client)

    result = await provider.complete(system="s", messages=[{"role": "user", "content": "q"}])

    assert result.text == "ok"
    assert client.messages.calls == 2


@pytest.mark.asyncio
async def test_terminal_error_is_not_retried() -> None:
    client = _Client([_status_error(400)])
    provider = ClaudeProvider(settings=_settings(), client=client)

    with pytest.raises(ClaudeProviderError) as caught:
        await provider.complete(system="s", messages=[{"role": "user", "content": "q"}])

    assert caught.value.retryable is False
    assert client.messages.calls == 1


@pytest.mark.asyncio
async def test_retry_budget_is_finite() -> None:
    client = _Client([_connection_error(), _connection_error(), _connection_error()])
    provider = ClaudeProvider(settings=_settings(CLAUDE_MAX_RETRIES=2), client=client)

    with pytest.raises(ClaudeProviderError) as caught:
        await provider.complete(system="s", messages=[{"role": "user", "content": "q"}])

    assert caught.value.retryable is True
    assert client.messages.calls == 3  # 1 initial + 2 retries
