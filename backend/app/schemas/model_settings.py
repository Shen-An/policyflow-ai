"""Independent runtime Chat and Embedding provider settings contracts."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, model_validator

ModelCapability = Literal["chat", "embedding"]


class ModelEndpointSettingsUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    auth_mode: Literal["bearer", "none"] = "bearer"
    api_style: Literal["openai_chat_completions", "openai_responses", "openai_embeddings"]
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    model: str = Field(min_length=1, max_length=100)
    embedding_dimension: int | None = Field(default=None, ge=1, le=100_000)
    embedding_input_type: Literal["none", "query", "passage"] | None = None
    timeout_seconds: float = Field(default=120.0, ge=1, le=600)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_provider(self) -> "ModelEndpointSettingsUpdate":
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be a credential-free HTTP(S) base URL")
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("api_key and clear_api_key cannot be used together")
        return self


class ModelEndpointSettingsRead(BaseModel):
    id: str
    capability: ModelCapability
    name: str
    provider_type: str
    base_url: str
    auth_mode: str
    api_style: str
    api_key_configured: bool
    api_key_source: Literal["database", "environment", "none"]
    model: str
    embedding_dimension: int | None
    embedding_input_type: Literal["none", "query", "passage"] | None
    timeout_seconds: float
    enabled: bool
    updated_at: datetime


class ModelProviderSettingsResponse(BaseModel):
    chat: ModelEndpointSettingsRead | None
    embedding: ModelEndpointSettingsRead | None


class ModelCatalogResponse(BaseModel):
    capability: ModelCapability
    models: list[str]


class ModelCapabilityResult(BaseModel):
    status: Literal["passed", "skipped", "failed"]
    message: str
    dimension: int | None = None
    error_code: str | None = None


class ModelProviderTestResponse(BaseModel):
    capability: ModelCapability
    result: ModelCapabilityResult
    request_id: str | None
