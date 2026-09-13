"""Unit tests for typed settings and production authority guards."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.core.config import Environment, Settings


def _production_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+psycopg://policyflow@postgres:5432/policyflow",
        "CELERY_BROKER_URL": "amqps://rabbitmq:5671/policyflow",
        "REDIS_URL": "rediss://redis:6379/0",
        "MILVUS_URI": "https://milvus:19530",
        "OBJECT_STORE_ENDPOINT_URL": "https://minio:9000",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318",
        "UPLOAD_DIR": "",
        "RAG_WORKSPACE_DIR": "",
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_profiles_allow_sqlite_and_local_paths(
    environment: str, tmp_path: Path
) -> None:
    settings = Settings(
        ENVIRONMENT=environment,  # type: ignore[arg-type]
        DATABASE_URL="sqlite+aiosqlite:///./policyflow.db",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.ENVIRONMENT == environment
    assert settings.UPLOAD_DIR == tmp_path / "uploads"
    assert settings.RAG_WORKSPACE_DIR == tmp_path / "rag"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///./policyflow.db",
        "sqlite+aiosqlite:///./policyflow.db",
        "SQLITE:///./policyflow.db",
        "SQLite+AiOsQlItE:///./policyflow.db",
    ],
)
def test_production_rejects_every_sqlite_url_form(database_url: str) -> None:
    with pytest.raises(ValidationError, match=r"DATABASE_URL.*SQLite"):
        _production_settings(DATABASE_URL=database_url)


@pytest.mark.parametrize("field", ["UPLOAD_DIR", "RAG_WORKSPACE_DIR"])
def test_production_rejects_host_local_file_authorities(field: str, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=field):
        _production_settings(**{field: tmp_path})


@pytest.mark.parametrize("field", ["UPLOAD_DIR", "RAG_WORKSPACE_DIR"])
def test_production_does_not_treat_dot_as_disabled(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _production_settings(**{field: "."})


def test_production_accepts_complete_enterprise_configuration() -> None:
    settings = _production_settings()

    assert settings.ENVIRONMENT is Environment.PRODUCTION
    assert settings.DATABASE_URL.startswith("postgresql+psycopg://")
    assert settings.UPLOAD_DIR == Path()
    assert settings.RAG_WORKSPACE_DIR == Path()


@pytest.mark.parametrize(
    ("override", "variable"),
    [
        ({"DATABASE_URL": "postgresql://postgres/policyflow"}, "DATABASE_URL"),
        ({"CELERY_BROKER_URL": "amqp://rabbitmq/policyflow"}, "CELERY_BROKER_URL"),
        ({"REDIS_URL": "redis://redis/0"}, "REDIS_URL"),
        ({"MILVUS_URI": "http://milvus:19530"}, "MILVUS_URI"),
        ({"MILVUS_TLS_ENABLED": False}, "MILVUS_TLS_ENABLED"),
        ({"MILVUS_TENANT_PARTITION_KEY": "workspace_id"}, "MILVUS_TENANT_PARTITION_KEY"),
        ({"OBJECT_STORE_ENDPOINT_URL": "http://minio:9000"}, "OBJECT_STORE_ENDPOINT_URL"),
        ({"OBJECT_STORE_VERSIONING_REQUIRED": False}, "OBJECT_STORE_VERSIONING_REQUIRED"),
        ({"OBJECT_STORE_TLS_ENABLED": False}, "OBJECT_STORE_TLS_ENABLED"),
        ({"CELERY_TASK_IGNORE_RESULT": False}, "CELERY_TASK_IGNORE_RESULT"),
        ({"CELERY_TASK_ACKS_LATE": False}, "CELERY_TASK_ACKS_LATE"),
        ({"SANDBOX_RUNTIME_CLASS_NAME": "runc"}, "SANDBOX_RUNTIME_CLASS_NAME"),
        ({"SANDBOX_SERVICE_ACCOUNT_AUTOMOUNT": True}, "SANDBOX_SERVICE_ACCOUNT_AUTOMOUNT"),
        ({"LIGHTRAG_BASE_URL": "https://lightrag.example.test"}, "LIGHTRAG_BASE_URL"),
    ],
)
def test_production_rejects_unsafe_enterprise_boundaries(
    override: dict[str, Any], variable: str
) -> None:
    with pytest.raises(ValidationError, match=variable):
        _production_settings(**override)


@pytest.mark.parametrize(
    "legacy_provider_field",
    ["LLM_BASE_URL", "LLM_CHAT_MODEL", "LLM_EMBEDDING_MODEL"],
)
def test_production_rejects_legacy_provider_bootstrap(legacy_provider_field: str) -> None:
    with pytest.raises(ValidationError, match=legacy_provider_field) as exc_info:
        _production_settings(**{legacy_provider_field: "secret-bearing-bootstrap-value"})

    assert "secret-bearing-bootstrap-value" not in str(exc_info.value)


def test_production_anthropic_execution_requires_sdk_credential_or_profile() -> None:
    with pytest.raises(ValidationError, match=r"ANTHROPIC_API_KEY or ANTHROPIC_PROFILE"):
        _production_settings(LLM_EXECUTION_MODE="anthropic")


def test_production_anthropic_execution_accepts_sdk_profile() -> None:
    settings = _production_settings(
        LLM_EXECUTION_MODE="anthropic", ANTHROPIC_PROFILE="production-profile"
    )

    assert settings.ANTHROPIC_PROFILE == "production-profile"


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ENVIRONMENT"):
        Settings(ENVIRONMENT="staging", _env_file=None)  # type: ignore[call-arg,arg-type]


def test_claude_defaults_are_centralized_and_strict() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.CLAUDE_PROVIDER == "anthropic"
    assert settings.CLAUDE_MODEL == "claude-opus-5"
    assert settings.CLAUDE_THINKING_TYPE == "adaptive"
    assert settings.CLAUDE_EFFORT == "high"
    assert settings.CLAUDE_STREAMING_ENABLED is True
    assert settings.CLAUDE_TIMEOUT_SECONDS == 120.0
    assert settings.CLAUDE_MAX_OUTPUT_TOKENS == 16_000
    assert settings.CLAUDE_MAX_RETRIES == 2


def test_claude_override_is_typed_and_normalized() -> None:
    settings = Settings(
        CLAUDE_EFFORT=" XHIGH ",  # type: ignore[arg-type]
        CLAUDE_STREAMING_ENABLED=False,
        CLAUDE_TIMEOUT_SECONDS=45,
        CLAUDE_MAX_OUTPUT_TOKENS=32_000,
        CLAUDE_MAX_RETRIES=4,
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.CLAUDE_EFFORT == "xhigh"
    assert settings.CLAUDE_STREAMING_ENABLED is False
    assert settings.CLAUDE_TIMEOUT_SECONDS == 45.0
    assert settings.CLAUDE_MAX_OUTPUT_TOKENS == 32_000
    assert settings.CLAUDE_MAX_RETRIES == 4


@pytest.mark.parametrize(
    ("override", "variable"),
    [
        ({"CLAUDE_PROVIDER": "openai"}, "CLAUDE_PROVIDER"),
        ({"CLAUDE_THINKING_TYPE": "enabled"}, "CLAUDE_THINKING_TYPE"),
        ({"CLAUDE_MODEL": "claude-sonnet-5"}, "CLAUDE_MODEL"),
    ],
)
def test_unsupported_claude_authority_is_rejected(
    override: dict[str, Any], variable: str
) -> None:
    with pytest.raises(ValidationError, match=variable):
        Settings(**override, _env_file=None)  # type: ignore[call-arg]


def test_unknown_dotenv_variable_is_not_silently_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ENVIRONMENT=development\nCLAUDE_PROVDER=anthropic\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="claude_provder"):
        Settings(_env_file=env_file)  # type: ignore[call-arg]
