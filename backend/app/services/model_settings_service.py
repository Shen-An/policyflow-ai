"""Secure independent Chat, Embedding, and Reranker provider settings."""

import os
from typing import Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError, ConflictError
from backend.app.core.mcp_security import encrypt_secret
from backend.app.db.models import ModelProvider, User, utc_now
from backend.app.schemas.model_settings import (
    ModelEndpointSettingsRead,
    ModelEndpointSettingsUpdate,
    ModelProviderSettingsResponse,
)
from backend.app.services.audit_service import record_audit

Capability = Literal["chat", "embedding", "reranker"]


def get_capability_provider(session: Session, capability: Capability) -> ModelProvider | None:
    provider_types = {"openai_compatible", "nvidia_rerank"} if capability == "reranker" else {"openai_compatible"}
    return session.exec(
        select(ModelProvider).where(
            ModelProvider.provider_type.in_(provider_types),
            ModelProvider.capability == capability,
        )
    ).first()


def _to_read(provider: ModelProvider) -> ModelEndpointSettingsRead:
    auth_mode = str(provider.config_json.get("auth_mode") or "environment")
    api_key_source: Literal["database", "environment", "none"] = (
        "database"
        if provider.api_key_ciphertext
        else "environment"
        if os.getenv(provider.api_key_env)
        else "none"
    )
    model = (
        provider.default_embedding_model or provider.default_chat_model
        if provider.capability == "embedding"
        else provider.default_chat_model
    )
    return ModelEndpointSettingsRead(
        id=provider.id,
        capability=cast(Capability, provider.capability),
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url or "",
        auth_mode=auth_mode,
        api_style=str(
            provider.config_json.get("api_style")
            or (
                "openai_embeddings"
                if provider.capability == "embedding"
                else "nvidia_rerank"
                if provider.capability == "reranker"
                else "openai_chat_completions"
            )
        ),
        api_key_configured=api_key_source != "none",
        api_key_source=api_key_source,
        model=model,
        embedding_dimension=(
            int(provider.config_json.get("embedding_dim", 1536))
            if provider.capability == "embedding"
            else None
        ),
        embedding_input_type=(
            cast(Literal["none", "query", "passage"], str(
                "none"
                if "integrate.api.nvidia.com" in (provider.base_url or "")
                else provider.config_json.get("embedding_input_type") or "none"
            ))
            if provider.capability == "embedding"
            else None
        ),
        timeout_seconds=float(provider.config_json.get("timeout_seconds", 120.0)),
        enabled=provider.enabled,
        updated_at=provider.updated_at,
    )


def get_model_provider_settings(session: Session) -> ModelProviderSettingsResponse:
    chat = get_capability_provider(session, "chat")
    embedding = get_capability_provider(session, "embedding")
    reranker = get_capability_provider(session, "reranker")
    return ModelProviderSettingsResponse(
        chat=_to_read(chat) if chat else None,
        embedding=_to_read(embedding) if embedding else None,
        reranker=_to_read(reranker) if reranker else None,
    )


def update_model_provider_settings(
    session: Session,
    settings: Settings,
    user: User,
    capability: Capability,
    data: ModelEndpointSettingsUpdate,
    ip_address: str | None = None,
) -> ModelEndpointSettingsRead:
    provider = get_capability_provider(session, capability)
    if provider is None:
        provider = ModelProvider(
            name=data.name,
            provider_type="nvidia_rerank" if capability == "reranker" else "openai_compatible",
            capability=capability,
            base_url=data.base_url,
            api_key_env=(
                settings.NVIDIA_RERANKER_API_KEY_ENV
                if capability == "reranker"
                else settings.LLM_API_KEY_ENV
            ),
            default_chat_model=data.model,
        )
    if capability == "chat" and data.api_style not in {
        "openai_chat_completions",
        "openai_responses",
    }:
        raise ApplicationError("MODEL_API_STYLE_INVALID", "Chat provider requires a Chat API style", 422)
    if capability == "embedding" and data.api_style != "openai_embeddings":
        raise ApplicationError("MODEL_API_STYLE_INVALID", "Embedding provider requires openai_embeddings API style", 422)
    if capability == "reranker" and data.api_style != "nvidia_rerank":
        raise ApplicationError("MODEL_API_STYLE_INVALID", "Reranker provider requires nvidia_rerank API style", 422)
    provider.name = data.name
    provider.provider_type = "nvidia_rerank" if capability == "reranker" else "openai_compatible"
    provider.capability = capability
    provider.base_url = data.base_url.rstrip("/")
    provider.api_key_env = (
        settings.NVIDIA_RERANKER_API_KEY_ENV if capability == "reranker" else settings.LLM_API_KEY_ENV
    )
    provider.default_chat_model = data.model
    provider.default_embedding_model = data.model if capability == "embedding" else None
    provider.enabled = data.enabled
    provider.config_json = {
        **provider.config_json,
        "auth_mode": data.auth_mode,
        "api_style": data.api_style,
        "timeout_seconds": data.timeout_seconds,
        **(
            {
                "embedding_dim": data.embedding_dimension or 1536,
                "embedding_input_type": (
                    "none"
                    if "integrate.api.nvidia.com" in data.base_url
                    else data.embedding_input_type or "none"
                ),
            }
            if capability == "embedding"
            else {
                "models": [
                    item.strip()
                    for item in settings.NVIDIA_RERANKER_MODELS.split(",")
                    if item.strip()
                ],
                "truncate": settings.NVIDIA_RERANKER_TRUNCATE,
            }
            if capability == "reranker"
            else {}
        ),
    }
    if data.api_key is not None:
        provider.api_key_ciphertext = encrypt_secret(
            data.api_key.get_secret_value(),
            settings.SECRET_KEY,
        )
    elif data.clear_api_key:
        provider.api_key_ciphertext = None
    provider.updated_at = utc_now()
    session.add(provider)
    try:
        session.flush()
        record_audit(
            session,
            action="settings.model_provider.update",
            target_type="model_provider",
            actor_id=user.id,
            target_id=provider.id,
            detail={
                "capability": capability,
                "name": provider.name,
                "base_url": provider.base_url,
                "auth_mode": data.auth_mode,
                "api_style": data.api_style,
                "model": data.model,
                "enabled": provider.enabled,
                "api_key_updated": data.api_key is not None or data.clear_api_key,
            },
            ip_address=ip_address,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            "MODEL_PROVIDER_NAME_EXISTS",
            "Model provider name already exists",
        ) from exc
    session.refresh(provider)
    return _to_read(provider)
