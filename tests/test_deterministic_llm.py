"""Contract tests for the offline deterministic LLM substitute."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.integrations.deterministic_llm import (
    DeterministicLLM,
    DeterministicLLMError,
    DeterministicLLMService,
)
from backend.app.rag.protocols import LLMMessage


def test_deterministic_result_and_tool_script_are_reproducible() -> None:
    async def scenario() -> tuple[dict, dict]:
        llm = DeterministicLLM(
            version="test-v1",
            tool_script=[{"name": "lookup_policy", "arguments": {"key": "travel"}}],
        )
        first = (await llm.complete("same prompt")).as_dict()
        llm.reset()
        second = (await llm.complete("same prompt")).as_dict()
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second
    assert first["provider"] == "deterministic_mock"
    assert first["stop_reason"] == "tool_use"


def test_error_script_injects_only_configured_attempt() -> None:
    async def scenario() -> tuple[DeterministicLLMError, str]:
        llm = DeterministicLLM(version="test-v1", error_script="1:timeout")
        with pytest.raises(DeterministicLLMError) as caught:
            await llm.complete("prompt")
        result = await llm.complete("prompt")
        return caught.value, result.content

    error, content = asyncio.run(scenario())
    assert error.code == "timeout"
    assert error.retryable is True
    assert content == "deterministic response [test-v1]"


def test_stream_is_local_and_chunks_fixed_response() -> None:
    async def scenario() -> list[str]:
        llm = DeterministicLLM(response_text="abcdefgh")
        return [chunk async for chunk in llm.stream("prompt", chunk_size=3)]

    assert asyncio.run(scenario()) == ["abc", "def", "gh"]


def test_protocol_adapter_returns_router_json_and_completion() -> None:
    async def scenario() -> tuple[str, str]:
        service = DeterministicLLMService()
        routed = await service.complete("你是企业制度问答路由器。只输出 JSON", "问题：差旅流程")
        completion = await service.complete_with_tools(
            [LLMMessage(role="user", content="answer")], []
        )
        return routed, completion.content or ""

    routed, answer = asyncio.run(scenario())
    assert '"task_type": "process_checklist"' in routed
    assert answer
