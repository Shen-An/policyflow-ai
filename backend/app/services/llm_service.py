"""OpenAI-compatible language-model service."""

import asyncio
import os

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.mcp_security import decrypt_secret
from backend.app.db.models import ModelProvider


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


class OpenAICompatibleLLMService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._client = client

    def _provider(self) -> ModelProvider | None:
        return get_enabled_provider(self.engine)

    @property
    def available(self) -> bool:
        return self._provider() is not None

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        provider = self._provider()
        if provider is None or provider.base_url is None:
            raise ApplicationError(
                "LLM_PROVIDER_ERROR", "No enabled LLM provider is configured", 503
            )
        api_style = str(provider.config_json.get("api_style") or "openai_chat_completions")
        headers = authorization_headers(provider, self.settings)
        if api_style == "openai_responses":
            endpoint = (
                provider.base_url
                if provider.base_url.rstrip("/").endswith("/responses")
                else f"{provider.base_url.rstrip('/')}/responses"
            )
            payload = {
                "model": provider.default_chat_model,
                "instructions": system_prompt,
                "input": user_prompt,
                "max_output_tokens": 1024,
            }
        else:
            endpoint = (
                provider.base_url
                if provider.base_url.rstrip("/").endswith("/chat/completions")
                else f"{provider.base_url.rstrip('/')}/chat/completions"
            )
            payload = {
                "model": provider.default_chat_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            }
        client = self._client or httpx.AsyncClient(
            timeout=float(
                provider.config_json.get("timeout_seconds", self.settings.LLM_TIMEOUT_SECONDS)
            )
        )
        owns_client = self._client is None
        try:
            response: httpx.Response | None = None
            for attempt in range(3):
                response = await client.post(endpoint, headers=headers, json=payload)
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                    break
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                await asyncio.sleep(min(delay, 10.0))
            if response is None:
                raise ValueError("LLM provider returned no response")
            response.raise_for_status()
            data = response.json()
            content = (
                _responses_output_text(data)
                if api_style == "openai_responses"
                else data["choices"][0]["message"]["content"]
            )
            if not isinstance(content, str) or not content.strip():
                raise ValueError("LLM response content is empty")
            return content.strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ApplicationError("LLM_PROVIDER_ERROR", "LLM request failed", 502) from exc
        finally:
            if owns_client:
                await client.aclose()

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
        client = self._client or httpx.AsyncClient(
            timeout=float(
                provider.config_json.get("timeout_seconds", self.settings.LLM_TIMEOUT_SECONDS)
            )
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
