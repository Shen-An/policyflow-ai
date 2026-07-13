"""OpenAI-compatible embedding service backed by runtime database settings."""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy.engine import Engine

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.services.llm_service import authorization_headers, get_enabled_provider

logger = logging.getLogger(__name__)

# Total attempts = 1 initial + 2 retries for transient failures.
_MAX_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 0.8
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _provider_request_id(response: httpx.Response) -> str | None:
    for header in ("nvcf-reqid", "x-request-id", "nvapi-request-id", "request-id"):
        value = response.headers.get(header)
        if value:
            return str(value)
    return None


def _upstream_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])[:300]
            if payload.get("message"):
                return str(payload["message"])[:300]
            if payload.get("detail"):
                return str(payload["detail"])[:300]
    except ValueError:
        pass
    return response.text.strip()[:300] or "Provider returned an empty error response"


def _network_error_message(exc: Exception, endpoint: str) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return (
            f"无法连接 Embedding 服务：连接超时（{endpoint}）。"
            "请检查网络、代理/VPN，以及模型设置中的 Base URL。"
        )
    if isinstance(exc, httpx.ReadTimeout):
        return (
            f"无法连接 Embedding 服务：读取响应超时（{endpoint}）。"
            "可稍后重试，或增大 Embedding 超时时间。"
        )
    if isinstance(exc, httpx.WriteTimeout):
        return (
            f"无法连接 Embedding 服务：发送请求超时（{endpoint}）。"
            "请检查网络稳定性后重试。"
        )
    if isinstance(exc, httpx.PoolTimeout):
        return (
            f"无法连接 Embedding 服务：连接池等待超时（{endpoint}）。"
            "请稍后重试。"
        )
    if isinstance(exc, httpx.ConnectError):
        return (
            f"无法连接 Embedding 服务：网络连接失败（{endpoint}）。"
            "常见原因：网络抖动、代理/VPN、DNS 或防火墙拦截。"
        )
    if isinstance(exc, httpx.ProxyError):
        return (
            f"无法连接 Embedding 服务：代理错误（{endpoint}）。"
            "请检查系统代理或企业网关配置。"
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"无法连接 Embedding 服务：请求超时（{endpoint}）。"
            "请检查网络或增大超时时间后重试。"
        )
    if isinstance(exc, httpx.NetworkError):
        return (
            f"无法连接 Embedding 服务：网络异常（{endpoint}）。"
            f"详情：{type(exc).__name__}"
        )
    return (
        f"Embedding 请求在获得有效响应前失败（{endpoint}）。"
        f"详情：{type(exc).__name__}"
    )


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.ProxyError,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
            httpx.TimeoutException,
        ),
    )


class OpenAICompatibleEmbeddingService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        max_attempts: int = _MAX_ATTEMPTS,
        retry_base_seconds: float = _RETRY_BASE_SECONDS,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._client = client
        self._max_attempts = max(1, max_attempts)
        self._retry_base_seconds = max(0.0, retry_base_seconds)

    @property
    def available(self) -> bool:
        provider = get_enabled_provider(self.engine, "embedding")
        return bool(
            provider
            and provider.base_url
            and (provider.default_embedding_model or provider.default_chat_model)
        )

    async def _sleep_before_retry(self, attempt: int) -> None:
        # attempt is 1-based completed try count; delay grows: 0.8s, 1.6s, ...
        delay = self._retry_base_seconds * (2 ** (attempt - 1))
        if delay > 0:
            await asyncio.sleep(delay)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        provider = get_enabled_provider(self.engine, "embedding")
        model = (
            provider.default_embedding_model or provider.default_chat_model
            if provider is not None
            else None
        )
        if provider is None or provider.base_url is None or not model:
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                "未配置已启用的 Embedding 模型，请先在「模型设置」中完成配置。",
                503,
            )
        endpoint = (
            provider.base_url
            if provider.base_url.rstrip("/").endswith("/embeddings")
            else f"{provider.base_url.rstrip('/')}/embeddings"
        )
        client = self._client or httpx.AsyncClient(
            timeout=float(provider.config_json.get("timeout_seconds", 120.0))
        )
        owns_client = self._client is None
        try:
            is_nvidia_hosted = "integrate.api.nvidia.com" in provider.base_url
            input_type = str(provider.config_json.get("embedding_input_type") or "query")
            base_payload: dict[str, object] = {
                "model": model,
                "input": texts,
                "encoding_format": "float",
            }
            payloads = [base_payload]
            if is_nvidia_hosted:
                nvidia_base = {**base_payload, "truncate": "NONE"}
                payloads = [
                    {
                        **nvidia_base,
                        "input_type": input_type if input_type != "none" else "query",
                    },
                    nvidia_base,
                ]
                if len(texts) == 1:
                    payloads.append({**nvidia_base, "input": texts[0]})
            elif input_type != "none":
                payloads = [{**base_payload, "input_type": input_type}]

            headers = authorization_headers(provider, self.settings)
            headers["Accept"] = "application/json"

            last_network_error: Exception | None = None
            last_http_failures: list[tuple[int, str | None, str]] = []

            for attempt in range(1, self._max_attempts + 1):
                http_failures: list[tuple[int, str | None, str]] = []
                response: httpx.Response | None = None
                try:
                    for candidate in payloads:
                        response = await client.post(
                            endpoint, headers=headers, json=candidate
                        )
                        if response.is_success:
                            data = response.json()["data"]
                            vectors = [item["embedding"] for item in data]
                            if len(vectors) != len(texts) or not all(
                                isinstance(item, list) for item in vectors
                            ):
                                raise ValueError("Embedding response shape is invalid")
                            if attempt > 1:
                                logger.info(
                                    "Embedding request succeeded after retry",
                                    extra={
                                        "attempt": attempt,
                                        "endpoint": endpoint,
                                        "text_count": len(texts),
                                    },
                                )
                            return [
                                [float(value) for value in vector] for vector in vectors
                            ]

                        status_code = response.status_code
                        request_id = _provider_request_id(response)
                        message = _upstream_error_message(response)
                        http_failures.append((status_code, request_id, message))
                        last_http_failures = http_failures

                        # Auth errors are not transient — fail immediately.
                        if status_code in {401, 403}:
                            raise ApplicationError(
                                "EMBEDDING_PROVIDER_ERROR",
                                (
                                    f"Embedding 鉴权失败（{status_code}）：{message}。"
                                    "请检查 API Key 是否正确、是否过期。"
                                ),
                                502,
                            )

                        # Keep payload-compatibility fallback only for non-auth failures.
                        # For retryable statuses, still try remaining payload shapes first,
                        # then retry the whole attempt with backoff.
                        continue

                    # All payload variants failed with HTTP responses.
                    retryable_http = any(
                        status in _RETRYABLE_STATUS_CODES
                        for status, _, _ in http_failures
                    )
                    if retryable_http and attempt < self._max_attempts:
                        logger.warning(
                            "Embedding provider returned retryable HTTP error; retrying",
                            extra={
                                "attempt": attempt,
                                "max_attempts": self._max_attempts,
                                "endpoint": endpoint,
                                "failures": http_failures,
                            },
                        )
                        await self._sleep_before_retry(attempt)
                        continue

                    attempts = "; ".join(
                        f"{status} provider_request_id={request_id or 'unavailable'} message={message}"
                        for status, request_id, message in http_failures
                    )
                    raise ApplicationError(
                        "EMBEDDING_PROVIDER_ERROR",
                        (
                            f"Embedding 服务返回错误（共 {len(http_failures)} 次兼容尝试）："
                            f"{attempts}"
                        ),
                        502,
                    )
                except ApplicationError:
                    raise
                except httpx.HTTPStatusError as exc:
                    # Should be rare because we check response.is_success above,
                    # but keep for completeness if raise_for_status is used elsewhere.
                    provider_message = _upstream_error_message(exc.response)
                    raise ApplicationError(
                        "EMBEDDING_PROVIDER_ERROR",
                        (
                            f"Embedding 请求失败（{exc.response.status_code}）："
                            f"{provider_message}"
                        ),
                        502,
                    ) from exc
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    last_network_error = exc
                    if _is_retryable_exception(exc) and attempt < self._max_attempts:
                        logger.warning(
                            "Embedding network error; retrying with backoff",
                            extra={
                                "attempt": attempt,
                                "max_attempts": self._max_attempts,
                                "endpoint": endpoint,
                                "error_type": type(exc).__name__,
                            },
                        )
                        await self._sleep_before_retry(attempt)
                        continue
                    if _is_retryable_exception(exc):
                        raise ApplicationError(
                            "EMBEDDING_PROVIDER_ERROR",
                            (
                                f"{_network_error_message(exc, endpoint)}"
                                f" 已重试 {self._max_attempts} 次仍失败。"
                            ),
                            502,
                        ) from exc
                    raise ApplicationError(
                        "EMBEDDING_PROVIDER_ERROR",
                        _network_error_message(exc, endpoint),
                        502,
                    ) from exc

            if last_network_error is not None:
                raise ApplicationError(
                    "EMBEDDING_PROVIDER_ERROR",
                    (
                        f"{_network_error_message(last_network_error, endpoint)}"
                        f" 已重试 {self._max_attempts} 次仍失败。"
                    ),
                    502,
                ) from last_network_error

            if last_http_failures:
                attempts = "; ".join(
                    f"{status} provider_request_id={request_id or 'unavailable'} message={message}"
                    for status, request_id, message in last_http_failures
                )
                raise ApplicationError(
                    "EMBEDDING_PROVIDER_ERROR",
                    f"Embedding 服务返回错误：{attempts}",
                    502,
                )

            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                f"无法连接 Embedding 服务（{endpoint}）。",
                502,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def list_models(self) -> list[str]:
        provider = get_enabled_provider(self.engine, "embedding")
        if provider is None or provider.base_url is None:
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                "未配置已启用的 Embedding 提供商，请先在「模型设置」中完成配置。",
                503,
            )
        catalog_base_url = provider.base_url.rstrip("/")
        if catalog_base_url.endswith("/embeddings"):
            catalog_base_url = catalog_base_url.removesuffix("/embeddings")
        endpoint = f"{catalog_base_url}/models"
        client = self._client or httpx.AsyncClient(
            timeout=float(provider.config_json.get("timeout_seconds", 120.0))
        )
        owns_client = self._client is None
        try:
            last_error: Exception | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.get(
                        endpoint,
                        headers=authorization_headers(provider, self.settings),
                    )
                    response.raise_for_status()
                    items = response.json()["data"]
                    return sorted(
                        str(item["id"])
                        for item in items
                        if isinstance(item, dict) and item.get("id")
                    )
                except httpx.HTTPStatusError as exc:
                    provider_message = _upstream_error_message(exc.response)
                    if (
                        exc.response.status_code in _RETRYABLE_STATUS_CODES
                        and attempt < self._max_attempts
                    ):
                        await self._sleep_before_retry(attempt)
                        continue
                    raise ApplicationError(
                        "EMBEDDING_PROVIDER_ERROR",
                        (
                            f"获取 Embedding 模型列表失败（{exc.response.status_code}）："
                            f"{provider_message}"
                        ),
                        502,
                    ) from exc
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    last_error = exc
                    if _is_retryable_exception(exc) and attempt < self._max_attempts:
                        await self._sleep_before_retry(attempt)
                        continue
                    if _is_retryable_exception(exc):
                        raise ApplicationError(
                            "EMBEDDING_PROVIDER_ERROR",
                            (
                                f"{_network_error_message(exc, endpoint)}"
                                f" 已重试 {self._max_attempts} 次仍失败。"
                            ),
                            502,
                        ) from exc
                    raise ApplicationError(
                        "EMBEDDING_PROVIDER_ERROR",
                        f"获取 Embedding 模型列表失败：{_network_error_message(exc, endpoint)}",
                        502,
                    ) from exc

            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                (
                    f"{_network_error_message(last_error or Exception('unknown'), endpoint)}"
                    f" 已重试 {self._max_attempts} 次仍失败。"
                ),
                502,
            )
        finally:
            if owns_client:
                await client.aclose()
