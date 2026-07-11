"""OpenAI-compatible embedding service backed by runtime database settings."""

import httpx
from sqlalchemy.engine import Engine

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.services.llm_service import authorization_headers, get_enabled_provider


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


class OpenAICompatibleEmbeddingService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._client = client

    @property
    def available(self) -> bool:
        provider = get_enabled_provider(self.engine, "embedding")
        return bool(
            provider
            and provider.base_url
            and (provider.default_embedding_model or provider.default_chat_model)
        )

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
                "No enabled embedding model is configured",
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
                    {**nvidia_base, "input_type": input_type if input_type != "none" else "query"},
                    nvidia_base,
                ]
                if len(texts) == 1:
                    payloads.append({**nvidia_base, "input": texts[0]})
            elif input_type != "none":
                payloads = [{**base_payload, "input_type": input_type}]
            headers = authorization_headers(provider, self.settings)
            headers["Accept"] = "application/json"
            failures: list[tuple[int, str | None, str]] = []
            response: httpx.Response | None = None
            for candidate in payloads:
                response = await client.post(endpoint, headers=headers, json=candidate)
                if response.is_success:
                    break
                failures.append(
                    (
                        response.status_code,
                        _provider_request_id(response),
                        _upstream_error_message(response),
                    )
                )
                if response.status_code in {401, 403}:
                    response.raise_for_status()
            if response is None or not response.is_success:
                attempts = "; ".join(
                    f"{status} provider_request_id={request_id or 'unavailable'} message={message}"
                    for status, request_id, message in failures
                )
                raise ApplicationError(
                    "EMBEDDING_PROVIDER_ERROR",
                    f"Embedding request failed after {len(failures)} compatibility attempt(s): {attempts}",
                    502,
                )
            data = response.json()["data"]
            vectors = [item["embedding"] for item in data]
            if len(vectors) != len(texts) or not all(isinstance(item, list) for item in vectors):
                raise ValueError("Embedding response shape is invalid")
            return [[float(value) for value in vector] for vector in vectors]
        except httpx.HTTPStatusError as exc:
            provider_message = _upstream_error_message(exc.response)
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                f"Embedding request failed ({exc.response.status_code}): {provider_message}",
                502,
            ) from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                "Embedding request failed before a valid provider response was received",
                502,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def list_models(self) -> list[str]:
        provider = get_enabled_provider(self.engine, "embedding")
        if provider is None or provider.base_url is None:
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                "No enabled embedding provider is configured",
                503,
            )
        catalog_base_url = provider.base_url.rstrip("/")
        if catalog_base_url.endswith("/embeddings"):
            catalog_base_url = catalog_base_url.removesuffix("/embeddings")
        client = self._client or httpx.AsyncClient(
            timeout=float(provider.config_json.get("timeout_seconds", 120.0))
        )
        owns_client = self._client is None
        try:
            response = await client.get(
                f"{catalog_base_url}/models",
                headers=authorization_headers(provider, self.settings),
            )
            response.raise_for_status()
            items = response.json()["data"]
            return sorted(
                str(item["id"])
                for item in items
                if isinstance(item, dict) and item.get("id")
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                "Embedding model catalog request failed",
                502,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
