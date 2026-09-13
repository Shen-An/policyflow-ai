"""A deterministic, offline-only LLM substitute for capacity and recovery tests.

The class deliberately has no HTTP client or provider SDK dependency.  Its output,
tool calls, delay, and injected failures are all supplied by local configuration so
that capacity results can be reproduced without contacting a real provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.rag.protocols import LLMCompletion, LLMMessage, ToolCallRequest


@dataclass(frozen=True, slots=True)
class DeterministicToolCall:
    """A provider-shaped tool call with stable identifiers."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = "tool-call-1"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True, slots=True)
class DeterministicLLMResult:
    """Serializable result returned by :class:`DeterministicLLM`."""

    content: str
    version: str
    attempt: int
    latency_ms: int
    tool_calls: tuple[DeterministicToolCall, ...] = ()
    prompt_sha256: str = ""
    stop_reason: str = "end_turn"

    @property
    def provider(self) -> str:
        return "deterministic_mock"

    @property
    def model(self) -> str:
        return self.version

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "version": self.version,
            "provider": self.provider,
            "model": self.model,
            "attempt": self.attempt,
            "latency_ms": self.latency_ms,
            "tool_calls": [call.as_dict() for call in self.tool_calls],
            "prompt_sha256": self.prompt_sha256,
            "stop_reason": self.stop_reason,
        }


class DeterministicLLMError(RuntimeError):
    """Configured provider failure that never leaves the local process."""

    def __init__(self, code: str, *, attempt: int, message: str | None = None) -> None:
        self.code = code
        self.attempt = attempt
        self.retryable = code.casefold() in {"timeout", "rate_limit", "unavailable", "transient"}
        super().__init__(message or f"deterministic mock error: {code} (attempt {attempt})")


def _parse_error_script(value: str | None) -> dict[int, str]:
    """Parse ``attempt:error`` pairs from ``LLM_MOCK_ERROR_SCRIPT``."""

    parsed: dict[int, str] = {}
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        attempt_text, separator, error = item.partition(":")
        if not separator:
            raise ValueError("LLM_MOCK_ERROR_SCRIPT entries must use attempt:error")
        try:
            attempt = int(attempt_text.strip())
        except ValueError as exc:
            raise ValueError(f"invalid deterministic mock attempt: {attempt_text!r}") from exc
        if attempt < 1 or not error.strip():
            raise ValueError(f"invalid deterministic mock error entry: {item!r}")
        parsed[attempt] = error.strip()
    return parsed


def _coerce_tool_call(
    value: DeterministicToolCall | Mapping[str, Any], index: int
) -> DeterministicToolCall:
    if isinstance(value, DeterministicToolCall):
        return value
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("deterministic tool calls require a non-empty name")
    arguments = value.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise ValueError("deterministic tool call arguments must be a mapping")
    call_id = value.get("id", f"tool-call-{index}")
    return DeterministicToolCall(
        id=str(call_id),
        name=name,
        arguments=dict(arguments),
    )


class DeterministicLLM:
    """Offline async LLM double with reproducible response and failure behavior."""

    provider = "deterministic_mock"
    mode = "deterministic_mock"

    def __init__(
        self,
        *,
        version: str = "policyflow-mock-v1",
        latency_ms: int = 0,
        error_script: str | Mapping[int, str] | None = None,
        response_text: str | None = None,
        tool_script: Sequence[DeterministicToolCall | Mapping[str, Any]] = (),
        artifact_dir: str | Path = "artifacts/load/mock",
    ) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if not version.strip():
            raise ValueError("version must be non-empty")
        self.version = version.strip()
        self.latency_ms = latency_ms
        self.error_script = (
            _parse_error_script(error_script)
            if isinstance(error_script, str) or error_script is None
            else {int(key): str(value) for key, value in error_script.items()}
        )
        self.response_text = response_text or f"deterministic response [{self.version}]"
        self.tool_script = tuple(
            _coerce_tool_call(value, index) for index, value in enumerate(tool_script, start=1)
        )
        self.artifact_dir = Path(artifact_dir)
        self._attempt = 0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DeterministicLLM:
        """Build a mock from the non-secret ``LLM_MOCK_*`` environment settings."""

        env = os.environ if environ is None else environ
        raw_latency = env.get("LLM_MOCK_LATENCY_MILLISECONDS", "0")
        try:
            latency_ms = int(raw_latency)
        except ValueError as exc:
            raise ValueError("LLM_MOCK_LATENCY_MILLISECONDS must be an integer") from exc
        return cls(
            version=env.get("LLM_MOCK_VERSION", "policyflow-mock-v1"),
            latency_ms=latency_ms,
            error_script=env.get("LLM_MOCK_ERROR_SCRIPT", ""),
            artifact_dir=env.get("LLM_MOCK_ARTIFACT_DIR", "artifacts/load/mock"),
        )

    def reset(self) -> None:
        """Reset the deterministic attempt counter between test scenarios."""

        self._attempt = 0

    async def complete(
        self,
        prompt: str,
        *,
        attempt: int | None = None,
        tool_script: Sequence[DeterministicToolCall | Mapping[str, Any]] | None = None,
    ) -> DeterministicLLMResult:
        """Return a local result after the configured delay or injected failure."""

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if attempt is None:
            self._attempt += 1
            current_attempt = self._attempt
        else:
            if attempt < 1:
                raise ValueError("attempt must be positive")
            current_attempt = attempt
            self._attempt = max(self._attempt, attempt)

        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        error_code = self.error_script.get(current_attempt)
        if error_code is not None:
            raise DeterministicLLMError(error_code, attempt=current_attempt)

        calls = (
            self.tool_script
            if tool_script is None
            else tuple(
                _coerce_tool_call(value, index) for index, value in enumerate(tool_script, start=1)
            )
        )
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return DeterministicLLMResult(
            content=self.response_text,
            version=self.version,
            attempt=current_attempt,
            latency_ms=self.latency_ms,
            tool_calls=calls,
            prompt_sha256=digest,
            stop_reason="tool_use" if calls else "end_turn",
        )

    async def invoke(self, prompt: str, **kwargs: Any) -> DeterministicLLMResult:
        """LangChain/provider-style alias for :meth:`complete`."""

        return await self.complete(prompt, **kwargs)

    async def stream(
        self,
        prompt: str,
        *,
        chunk_size: int = 32,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Yield deterministic content chunks without opening a provider connection."""

        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        result = await self.complete(prompt, **kwargs)
        for start in range(0, len(result.content), chunk_size):
            yield result.content[start : start + chunk_size]


class DeterministicLLMService:
    """Protocol adapter used by route-level baseline tests.

    ``DeterministicLLM`` remains a small provider-shaped primitive for unit tests;
    this adapter exposes the application's two-prompt and tool-loop contracts.
    It never imports an HTTP client or contacts an external provider.
    """

    def __init__(self, llm: DeterministicLLM | None = None) -> None:
        self.llm = llm or DeterministicLLM()

    @property
    def available(self) -> bool:
        return True

    @staticmethod
    def _router_response(user_prompt: str) -> str:
        lowered = user_prompt.lower()
        task_type = (
            "process_checklist"
            if any(term in lowered for term in ("流程", "步骤", "清单", "process", "travel"))
            else "knowledge_qa"
        )
        need_skill = task_type == "process_checklist"
        return json.dumps(
            {
                "domain": "general",
                "task_type": task_type,
                "risk_level": "low",
                "need_skill": need_skill,
                "tool_hints": ["skill.run"] if need_skill else [],
                "rewrite_query": None,
                "complexity": "simple",
                "difficulty": "simple",
                "plan_steps": [],
            },
            ensure_ascii=False,
        )

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return valid router JSON or a stable answer for normal prompts."""

        if "路由器" in system_prompt or "只输出 JSON" in system_prompt:
            return self._router_response(user_prompt)
        result = await self.llm.complete(f"{system_prompt}\n{user_prompt}")
        return result.content

    async def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMCompletion:
        """Implement the application's optional tool-loop contract offline."""

        prompt = "\n".join(message.content or "" for message in messages)
        result = await self.llm.complete(prompt, tool_script=self.llm.tool_script if tools else ())
        calls = [
            ToolCallRequest(id=call.id, name=call.name, arguments=call.arguments)
            for call in result.tool_calls
        ]
        return LLMCompletion(content=result.content, tool_calls=calls)

    async def stream(self, system_prompt: str, user_prompt: str, *, chunk_size: int = 32):
        """Yield answer chunks through the same adapter used by chat routes."""

        result = await self.complete(system_prompt, user_prompt)
        for start in range(0, len(result), chunk_size):
            yield result[start : start + chunk_size]
