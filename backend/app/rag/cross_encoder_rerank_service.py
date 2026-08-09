"""NVIDIA-hosted Cross-Encoder reranker with rotating model fallback."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import httpx

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.mcp_security import decrypt_secret
from backend.app.db.models import ModelProvider
from backend.app.core.logging import get_logger
from backend.app.schemas.retrieval import Evidence

logger = get_logger(__name__)

DEFAULT_NVIDIA_RERANKER_MODELS = (
    "nvidia/llama-nemotron-rerank-vl-1b-v2",
    "nvidia/llama-nemotron-rerank-1b-v2",
    "nvidia/rerank-qa-mistral-4b",
)


class NvidiaCrossEncoderRerankService:
    """Call NVIDIA rerank models with round-robin starts and per-request fallback.

    The service intentionally does not silently fall back to lexical fusion.  If the
    Cross-Encoder backend is selected and every configured NVIDIA model fails, the
    request fails explicitly so traces and evaluation results do not misrepresent the
    rerank method.
    """

    def __init__(
        self,
        *,
        models: Sequence[str] = DEFAULT_NVIDIA_RERANKER_MODELS,
        base_url: str = "https://ai.api.nvidia.com",
        endpoint_template: str = "/v1/retrieval/{model}/reranking",
        api_key: str | None = None,
        api_key_env: str = "NVIDIA_API_KEY",
        timeout_seconds: float = 30.0,
        truncate: str = "END",
        client: httpx.AsyncClient | None = None,
        engine: Engine | None = None,
        settings: Settings | None = None,
    ) -> None:
        normalized_models = tuple(item.strip() for item in models if item.strip())
        self.models = normalized_models
        self.base_url = base_url.rstrip("/")
        self.endpoint_template = endpoint_template
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.truncate = truncate
        self.engine = engine
        self.settings = settings
        self._client = client
        self._next_model_index = 0
        self._rotation_lock = asyncio.Lock()

    def _runtime_config(self) -> tuple[tuple[str, ...], str | None, str, float, str]:
        models = self.models
        api_key = self.api_key
        base_url = self.base_url
        timeout_seconds = self.timeout_seconds
        truncate = self.truncate

        if self.engine is not None and self.settings is not None:
            with Session(self.engine) as session:
                provider = session.exec(
                    select(ModelProvider).where(ModelProvider.capability == "reranker")
                ).first()
            if provider is not None:
                if not provider.enabled:
                    return (), None, base_url, timeout_seconds, truncate
                if provider.api_key_ciphertext:
                    api_key = decrypt_secret(provider.api_key_ciphertext, self.settings.SECRET_KEY)
                else:
                    api_key = os.getenv(provider.api_key_env)
                configured_models = provider.config_json.get("models")
                if isinstance(configured_models, list):
                    models = tuple(str(item).strip() for item in configured_models if str(item).strip())
                base_url = (provider.base_url or base_url).rstrip("/")
                timeout_seconds = max(
                    1.0,
                    float(provider.config_json.get("timeout_seconds", timeout_seconds)),
                )
                truncate = str(provider.config_json.get("truncate") or truncate)
            elif api_key is None:
                api_key = os.getenv(self.api_key_env)
        elif api_key is None:
            api_key = os.getenv(self.api_key_env)

        return models, api_key, base_url, timeout_seconds, truncate

    @property
    def available(self) -> bool:
        models, api_key, _, _, _ = self._runtime_config()
        return bool(models and api_key)

    async def _start_index(self, model_count: int) -> int:
        async with self._rotation_lock:
            index = self._next_model_index % model_count
            self._next_model_index = (index + 1) % model_count
            return index

    async def _set_next_after(self, model_index: int, model_count: int) -> None:
        async with self._rotation_lock:
            self._next_model_index = (model_index + 1) % model_count

    def _endpoint(self, model: str, base_url: str) -> str:
        path = self.endpoint_template.format(model=model)
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _passage_text(item: Evidence) -> str:
        title = (item.document_title or "").strip()
        snippet = item.snippet.strip()
        return f"{title}\n{snippet}" if title else snippet

    @staticmethod
    def _extract_rankings(data: object) -> list[tuple[int, float]]:
        if not isinstance(data, dict):
            raise ValueError("NVIDIA reranker response must be an object")

        raw_rankings: object = data.get("rankings")
        if raw_rankings is None:
            raw_rankings = data.get("results")
        if raw_rankings is None:
            raw_rankings = data.get("data")
        if not isinstance(raw_rankings, list):
            raise ValueError("NVIDIA reranker response has no rankings list")

        rankings: list[tuple[int, float]] = []
        for position, raw_item in enumerate(raw_rankings):
            if isinstance(raw_item, int | float):
                rankings.append((position, float(raw_item)))
                continue
            if not isinstance(raw_item, dict):
                continue

            raw_index = raw_item.get("index")
            if raw_index is None:
                raw_index = raw_item.get("passage_index")
            if raw_index is None:
                raw_index = raw_item.get("document_index")
            raw_score = raw_item.get("logit")
            if raw_score is None:
                raw_score = raw_item.get("score")
            if raw_score is None:
                raw_score = raw_item.get("relevance_score")
            if raw_index is None or raw_score is None:
                continue
            rankings.append((int(raw_index), float(raw_score)))

        if not rankings:
            raise ValueError("NVIDIA reranker response has no usable rankings")
        return rankings

    async def _rerank_with_model(
        self,
        model: str,
        query: str,
        candidates: Sequence[Evidence],
        limit: int,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        truncate: str,
    ) -> list[Evidence]:
        payload = {
            "model": model,
            "query": {"text": query},
            "passages": [{"text": self._passage_text(item)} for item in candidates],
            "truncate": truncate,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=timeout_seconds)
        owns_client = self._client is None
        try:
            response = await client.post(self._endpoint(model, base_url), headers=headers, json=payload)
            response.raise_for_status()
            rankings = self._extract_rankings(response.json())
            if len(rankings) != len(candidates):
                raise ValueError(
                    f"NVIDIA reranker returned {len(rankings)} rankings for "
                    f"{len(candidates)} passages"
                )

            seen: set[int] = set()
            scored: list[tuple[float, int, Evidence]] = []
            for index, score in rankings:
                if index < 0 or index >= len(candidates) or index in seen:
                    raise ValueError("NVIDIA reranker returned invalid passage indexes")
                seen.add(index)
                scored.append((score, index, candidates[index]))
            if len(seen) != len(candidates):
                raise ValueError("NVIDIA reranker omitted one or more passages")

            scored.sort(key=lambda row: (-row[0], row[1]))
            reranked: list[Evidence] = []
            for rank, (score, _, item) in enumerate(scored[:limit], start=1):
                metadata: dict[str, Any] = dict(item.metadata or {})
                metadata.update(
                    {
                        "rerank_method": "cross_encoder",
                        "rerank_provider": "nvidia",
                        "rerank_model": model,
                        "rerank_score": round(score, 6),
                    }
                )
                reranked.append(
                    item.model_copy(
                        update={
                            "rank": rank,
                            "rerank_score": score,
                            "metadata": metadata,
                        }
                    )
                )
            return reranked
        finally:
            if owns_client:
                await client.aclose()

    async def rerank(
        self,
        query: str,
        candidates: Sequence[Evidence],
        limit: int,
    ) -> list[Evidence]:
        if limit <= 0 or not candidates:
            return []
        models, api_key, base_url, timeout_seconds, truncate = self._runtime_config()
        if not models or not api_key:
            raise ApplicationError(
                "RERANKER_UNAVAILABLE",
                "NVIDIA Cross-Encoder reranker is not configured",
                503,
            )

        start_index = await self._start_index(len(models))
        failures: list[dict[str, str]] = []
        for offset in range(len(models)):
            model_index = (start_index + offset) % len(models)
            model = models[model_index]
            try:
                result = await self._rerank_with_model(
                    model,
                    query,
                    candidates,
                    limit,
                    api_key=api_key,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    truncate=truncate,
                )
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                failures.append({"model": model, "error": type(exc).__name__})
                logger.warning(
                    "NVIDIA Cross-Encoder reranker model failed; trying fallback",
                    extra={"model": model, "error_type": type(exc).__name__},
                )
                continue
            await self._set_next_after(model_index, len(models))
            return result

        raise ApplicationError(
            "RERANKER_UNAVAILABLE",
            "All configured NVIDIA Cross-Encoder reranker models failed",
            503,
            {"models": list(models), "failures": failures},
        )

