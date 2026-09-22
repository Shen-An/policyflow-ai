"""Official Anthropic async provider for the shared graph (T050).

All Claude configuration lives here, not scattered across graph nodes: model,
thinking mode, output budget, timeout and the finite retry policy come from one
:class:`Settings` surface. The graph's ``generate`` node calls
:meth:`ClaudeProvider.complete` and gets back a typed result or a typed,
classified error.

Retry policy (bounded, deadline-aware):

- Only connection/timeout/429/5xx are retried; everything else is terminal.
- Retries respect the run deadline and any ``Retry-After`` the provider sends,
  and never exceed the configured attempt cap.
- Errors are surfaced as :class:`ClaudeProviderError` with a stable code and a
  ``retryable`` flag; raw provider payloads and credentials never propagate.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from backend.app.core.config import Settings, get_settings

__all__ = [
    "ClaudeCompletion",
    "ClaudeProvider",
    "ClaudeProviderError",
    "MessageClient",
]

# Provider failures that are safe to retry within the run deadline.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.ServiceUnavailableError,
    anthropic.OverloadedError,
)


class ClaudeProviderError(Exception):
    """Typed, sanitized provider failure."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class ClaudeCompletion:
    """Structured result read from the final message."""

    text: str
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    raw_thinking: str | None = None


class MessageClient(Protocol):
    """The slice of the Anthropic async client this provider needs.

    Declared as a Protocol so tests can inject a fake without a network call.
    """

    @property
    def messages(self) -> Any: ...


@dataclass
class ClaudeProvider:
    settings: Settings = field(default_factory=get_settings)
    client: MessageClient | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            api_key = self.settings.ANTHROPIC_API_KEY
            self.client = anthropic.AsyncAnthropic(
                api_key=api_key.get_secret_value() if api_key else None,
                timeout=self.settings.CLAUDE_TIMEOUT_SECONDS,
                max_retries=0,  # retries are owned here, deadline-aware
            )

    def _request_kwargs(self, *, system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.CLAUDE_MODEL,
            "max_tokens": self.settings.CLAUDE_MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": messages,
        }
        # Adaptive thinking for complex requests when the configured model
        # supports it; kept as one central switch.
        if self.settings.CLAUDE_THINKING_TYPE == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        return kwargs

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        deadline_epoch: float | None = None,
    ) -> ClaudeCompletion:
        attempts = self.settings.CLAUDE_MAX_RETRIES + 1
        last_error: ClaudeProviderError | None = None
        for attempt in range(1, attempts + 1):
            if deadline_epoch is not None and time.monotonic() >= deadline_epoch:
                raise ClaudeProviderError(
                    "DEADLINE_EXCEEDED", "run deadline exceeded before provider call",
                    retryable=False,
                )
            try:
                return await self._invoke_once(system=system, messages=messages)
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = ClaudeProviderError(
                    "PROVIDER_RETRYABLE",
                    f"retryable provider error ({type(exc).__name__})",
                    retryable=True,
                )
                if attempt >= attempts:
                    break
                await self._sleep_before_retry(exc, attempt, deadline_epoch)
            except anthropic.APIStatusError as exc:
                raise ClaudeProviderError(
                    "PROVIDER_TERMINAL",
                    f"terminal provider error (status {exc.status_code})",
                    retryable=False,
                ) from None
            except anthropic.AnthropicError as exc:
                raise ClaudeProviderError(
                    "PROVIDER_TERMINAL",
                    f"terminal provider error ({type(exc).__name__})",
                    retryable=False,
                ) from None
        assert last_error is not None
        raise last_error

    async def _invoke_once(
        self, *, system: str, messages: list[dict[str, Any]]
    ) -> ClaudeCompletion:
        assert self.client is not None
        kwargs = self._request_kwargs(system=system, messages=messages)
        if self.settings.CLAUDE_STREAMING_ENABLED:
            async with self.client.messages.stream(**kwargs) as stream:
                final = await stream.get_final_message()
            return _completion_from_message(final)
        message = await self.client.messages.create(**kwargs)
        return _completion_from_message(message)

    async def _sleep_before_retry(
        self, exc: Exception, attempt: int, deadline_epoch: float | None
    ) -> None:
        delay = _retry_after_seconds(exc)
        if delay is None:
            delay = min(2.0 ** (attempt - 1), 8.0)  # bounded exponential backoff
        if deadline_epoch is not None:
            remaining = deadline_epoch - time.monotonic()
            if remaining <= 0:
                return
            delay = min(delay, remaining)
        await asyncio.sleep(max(0.0, delay))


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _completion_from_message(message: Any) -> ClaudeCompletion:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", ""))
        elif block_type in {"thinking", "redacted_thinking"}:
            thinking_parts.append(getattr(block, "thinking", "") or "")
    usage = getattr(message, "usage", None)
    return ClaudeCompletion(
        text="".join(text_parts),
        stop_reason=getattr(message, "stop_reason", None),
        input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        raw_thinking="".join(thinking_parts) or None,
    )
