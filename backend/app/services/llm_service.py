"""OpenAI-compatible language-model service."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_logger
from backend.app.core.mcp_security import decrypt_secret
from backend.app.db.models import ModelProvider
from backend.app.rag.protocols import LLMCompletion, LLMMessage, ToolCallRequest

logger = get_logger(__name__)

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _retry_delay_seconds(
    response: httpx.Response | None,
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), max_seconds)
            except ValueError:
                pass
    # Exponential backoff with light linear jitter so multi-KB bursts desync.
    delay = base_seconds * (2**attempt) + 0.25 * attempt
    return min(delay, max_seconds)


def _responses_output_text(data: dict[str, object]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
    raise ValueError("Responses API output text is empty")


def get_enabled_provider(engine: Engine, capability: str = "chat") -> ModelProvider | None:
    with Session(engine) as session:
        return session.exec(
            select(ModelProvider).where(
                col(ModelProvider.enabled).is_(True),
                ModelProvider.provider_type == "openai_compatible",
                ModelProvider.capability == capability,
            )
        ).first()


def authorization_headers(provider: ModelProvider, settings: Settings) -> dict[str, str]:
    auth_mode = str(provider.config_json.get("auth_mode") or "environment")
    if auth_mode == "none":
        return {}
    api_key = (
        decrypt_secret(provider.api_key_ciphertext, settings.SECRET_KEY)
        if provider.api_key_ciphertext
        else os.getenv(provider.api_key_env)
    )
    if not api_key:
        raise ApplicationError(
            "LLM_PROVIDER_ERROR",
            "The configured model API key is missing",
            503,
        )
    return {"Authorization": f"Bearer {api_key}"}


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": raw}


def _tool_calls_from_message(message: dict[str, Any]) -> list[ToolCallRequest]:
    raw_calls = message.get("tool_calls") or []
    results: list[ToolCallRequest] = []
    if not isinstance(raw_calls, list):
        return results
    for index, item in enumerate(raw_calls):
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or item.get("name") or "").strip()
        if not name:
            continue
        call_id = str(item.get("id") or f"call_{index}")
        results.append(
            ToolCallRequest(
                id=call_id,
                name=name,
                arguments=_parse_tool_arguments(function.get("arguments")),
            )
        )
    return results


def _to_openai_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role}
        if message.content is not None:
            item["content"] = message.content
        if message.role == "assistant" and message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
            item.setdefault("content", None)
        if message.role == "tool":
            item["tool_call_id"] = message.tool_call_id or ""
            if message.name:
                item["name"] = message.name
        payload.append(item)
    return payload


class OpenAICompatibleLLMService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        max_concurrency: int | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        retry_max_seconds: float | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._client = client
        concurrency = max(1, int(max_concurrency or settings.LLM_MAX_CONCURRENCY))
        self._max_attempts = max(1, int(max_attempts or settings.LLM_MAX_ATTEMPTS))
        self._retry_base_seconds = float(
            retry_base_seconds if retry_base_seconds is not None else settings.LLM_RETRY_BASE_SECONDS
        )
        self._retry_max_seconds = float(
            retry_max_seconds if retry_max_seconds is not None else settings.LLM_RETRY_MAX_SECONDS
        )
        # Process-wide gate so LightRAG keyword extraction + answer/tool loops
        # cannot stampede strict providers (SenseNova 429).
        self._request_semaphore = asyncio.Semaphore(concurrency)

    def _provider(self) -> ModelProvider | None:
        return get_enabled_provider(self.engine)

    @property
    def available(self) -> bool:
        return self._provider() is not None

    def _timeout(self, provider: ModelProvider) -> float:
        return float(
            provider.config_json.get("timeout_seconds", self.settings.LLM_TIMEOUT_SECONDS)
        )

    async def _post_json(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=timeout)
        owns_client = self._client is None
        try:
            async with self._request_semaphore:
                response: httpx.Response | None = None
                last_error: Exception | None = None
                for attempt in range(self._max_attempts):
                    try:
                        response = await client.post(endpoint, headers=headers, json=payload)
                    except (
                        httpx.ConnectError,
                        httpx.ConnectTimeout,
                        httpx.ReadTimeout,
                        httpx.WriteTimeout,
                        httpx.PoolTimeout,
                        httpx.ProxyError,
                        httpx.RemoteProtocolError,
                        httpx.NetworkError,
                        httpx.TimeoutException,
                    ) as exc:
                        last_error = exc
                        if attempt >= self._max_attempts - 1:
                            break
                        delay = _retry_delay_seconds(
                            None,
                            attempt,
                            base_seconds=self._retry_base_seconds,
                            max_seconds=self._retry_max_seconds,
                        )
                        logger.warning(
                            "LLM network error; retrying",
                            extra={
                                "attempt": attempt + 1,
                                "max_attempts": self._max_attempts,
                                "delay_seconds": delay,
                                "error_type": type(exc).__name__,
                            },
                        )
                        await asyncio.sleep(delay)
                        continue

                    if (
                        response.status_code not in _RETRYABLE_STATUS_CODES
                        or attempt >= self._max_attempts - 1
                    ):
                        break
                    delay = _retry_delay_seconds(
                        response,
                        attempt,
                        base_seconds=self._retry_base_seconds,
                        max_seconds=self._retry_max_seconds,
                    )
                    logger.warning(
                        "LLM provider rate-limited or unavailable; retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_attempts": self._max_attempts,
                            "status_code": response.status_code,
                            "delay_seconds": delay,
                        },
                    )
                    await asyncio.sleep(delay)

            if response is None:
                if last_error is not None:
                    raise last_error
                raise ValueError("LLM provider returned no response")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("LLM response is not a JSON object")
            return data
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            message = "LLM request failed"
            if status == 429:
                message = (
                    "LLM provider rate limited (429). "
                    "Reduce concurrent knowledge bases / hybrid keyword calls, "
                    "or switch to a higher-QPS provider."
                )
            raise ApplicationError("LLM_PROVIDER_ERROR", message, 502) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        result = await self.complete_with_tools(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ],
            tools=[],
        )
        content = (result.content or "").strip()
        if not content:
            raise ApplicationError("LLM_PROVIDER_ERROR", "LLM response content is empty", 502)
        return content

    async def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMCompletion:
        provider = self._provider()
        if provider is None or provider.base_url is None:
            raise ApplicationError(
                "LLM_PROVIDER_ERROR", "No enabled LLM provider is configured", 503
            )
        api_style = str(provider.config_json.get("api_style") or "openai_chat_completions")
        headers = authorization_headers(provider, self.settings)

        # Tool calling is only supported on chat.completions style providers.
        if api_style == "openai_responses" or not tools:
            if api_style == "openai_responses":
                system_prompt = next(
                    (item.content or "" for item in messages if item.role == "system"),
                    "",
                )
                user_chunks = [
                    item.content or ""
                    for item in messages
                    if item.role in {"user", "tool", "assistant"} and item.content
                ]
                endpoint = (
                    provider.base_url
                    if provider.base_url.rstrip("/").endswith("/responses")
                    else f"{provider.base_url.rstrip('/')}/responses"
                )
                payload = {
                    "model": provider.default_chat_model,
                    "instructions": system_prompt,
                    "input": "\n".join(user_chunks),
                    "max_output_tokens": 1024,
                }
                data = await self._post_json(
                    endpoint, headers, payload, self._timeout(provider)
                )
                text = _responses_output_text(data).strip()
                return LLMCompletion(content=text, tool_calls=[])

            endpoint = (
                provider.base_url
                if provider.base_url.rstrip("/").endswith("/chat/completions")
                else f"{provider.base_url.rstrip('/')}/chat/completions"
            )
            payload = {
                "model": provider.default_chat_model,
                "messages": _to_openai_messages(messages),
                "temperature": 0.1,
            }
            data = await self._post_json(endpoint, headers, payload, self._timeout(provider))
            message = data["choices"][0]["message"]
            content = message.get("content")
            text = content.strip() if isinstance(content, str) else None
            return LLMCompletion(content=text, tool_calls=_tool_calls_from_message(message))

        endpoint = (
            provider.base_url
            if provider.base_url.rstrip("/").endswith("/chat/completions")
            else f"{provider.base_url.rstrip('/')}/chat/completions"
        )
        payload = {
            "model": provider.default_chat_model,
            "messages": _to_openai_messages(messages),
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        data = await self._post_json(endpoint, headers, payload, self._timeout(provider))
        message = data["choices"][0]["message"]
        content = message.get("content")
        text = content.strip() if isinstance(content, str) and content.strip() else None
        return LLMCompletion(content=text, tool_calls=_tool_calls_from_message(message))

    async def list_models(self) -> list[str]:
        provider = self._provider()
        if provider is None or provider.base_url is None:
            raise ApplicationError(
                "LLM_PROVIDER_ERROR",
                "No enabled model provider is configured",
                503,
            )
        catalog_base_url = provider.base_url.rstrip("/")
        if catalog_base_url.endswith("/chat/completions"):
            catalog_base_url = catalog_base_url.removesuffix("/chat/completions")
        elif catalog_base_url.endswith("/responses"):
            catalog_base_url = catalog_base_url.removesuffix("/responses")
        client = self._client or httpx.AsyncClient(timeout=self._timeout(provider))
        owns_client = self._client is None
        try:
            response = await client.get(
                f"{catalog_base_url}/models",
                headers=authorization_headers(provider, self.settings),
            )
            response.raise_for_status()
            items = response.json()["data"]
            return sorted(
                str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ApplicationError(
                "LLM_PROVIDER_ERROR",
                "Model catalog request failed",
                502,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
