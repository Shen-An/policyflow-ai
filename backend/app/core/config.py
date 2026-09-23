"""Typed application configuration and production authority guards."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]
ClaudeEffort = Literal["low", "medium", "high", "xhigh", "max"]
_DISABLED_LOCAL_PATH = Path()


class Environment(StrEnum):
    """Supported deployment profiles."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Environment-backed settings with fail-fast production validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        case_sensitive=False,
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
    )

    PROJECT_NAME: str = "PolicyFlow AI"
    VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    DATABASE_URL: str = "sqlite:///./policyflow.db"
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: PositiveInt = 10
    DATABASE_MAX_OVERFLOW: NonNegativeInt = 0
    DATABASE_POOL_TIMEOUT_SECONDS: PositiveFloat = 30.0
    DATABASE_POOL_RECYCLE_SECONDS: PositiveInt = 1800
    DATABASE_POOL_PRE_PING: bool = True
    DATABASE_CONNECT_TIMEOUT_SECONDS: PositiveFloat = 10.0
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: PositiveInt = 30
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = Path("logs")
    LOG_FILE_NAME: str = "policyflow.log"
    BOOTSTRAP_ADMIN_USERNAME: str = "admin"
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@example.com"
    BOOTSTRAP_ADMIN_DISPLAY_NAME: str = "系统管理员"
    BOOTSTRAP_ADMIN_PASSWORD: str | None = None

    CELERY_BROKER_URL: str | None = None
    CELERY_TASK_IGNORE_RESULT: bool = True
    CELERY_TASK_DEFAULT_QUEUE: str = "policyflow.default"
    CELERY_WORKER_PREFETCH_MULTIPLIER: PositiveInt = 1
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS: PositiveFloat = 10.0
    RABBITMQ_QUEUE_TYPE: Literal["quorum"] = "quorum"
    RABBITMQ_QUEUE_MAX_LENGTH: PositiveInt = 10_000
    RABBITMQ_QUEUE_MAX_BYTES: PositiveInt = 268_435_456
    RABBITMQ_DEAD_LETTER_QUEUE: str = "policyflow.dead"

    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_KEY_PREFIX: str = "policyflow"
    REDIS_COORDINATION_PREFIX: str = "coord"
    REDIS_SSE_STREAM_PREFIX: str = "sse"
    REDIS_CONNECT_TIMEOUT_SECONDS: PositiveFloat = 5.0
    REDIS_SOCKET_TIMEOUT_SECONDS: PositiveFloat = 5.0

    MILVUS_URI: str = "https://milvus:19530"
    MILVUS_TOKEN: SecretStr | None = None
    MILVUS_TLS_ENABLED: bool = True
    MILVUS_SERVER_NAME: str = "milvus"
    MILVUS_DATABASE: str = "policyflow"
    MILVUS_COLLECTION: str = "policyflow_chunks"
    MILVUS_TENANT_PARTITION_KEY: str = "tenant_id"
    MILVUS_REQUEST_TIMEOUT_SECONDS: PositiveFloat = 30.0

    OBJECT_STORE_PROVIDER: Literal["s3", "minio"] = "s3"
    OBJECT_STORE_ENDPOINT_URL: str = "https://minio:9000"
    OBJECT_STORE_REGION: str = "us-east-1"
    OBJECT_STORE_BUCKET: str = "policyflow-materials"
    OBJECT_STORE_VERSIONING_REQUIRED: bool = True
    OBJECT_STORE_TLS_ENABLED: bool = True
    OBJECT_STORE_ACCESS_KEY_ID: SecretStr | None = None
    OBJECT_STORE_SECRET_ACCESS_KEY: SecretStr | None = None
    OBJECT_STORE_SESSION_TOKEN: SecretStr | None = None
    OBJECT_STORE_CONNECT_TIMEOUT_SECONDS: PositiveFloat = 10.0
    OBJECT_STORE_READ_TIMEOUT_SECONDS: PositiveFloat = 60.0

    OTEL_SERVICE_NAME: str = "policyflow-api"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://otel-collector:4318"
    OTEL_EXPORTER_OTLP_PROTOCOL: Literal["http/protobuf"] = "http/protobuf"
    OTEL_EXPORTER_OTLP_HEADERS: SecretStr | None = None
    OTEL_TRACES_SAMPLER: str = "parentbased_traceidratio"
    OTEL_TRACES_SAMPLER_ARG: UnitFloat = 0.10
    OTEL_METRICS_EXPORTER: Literal["otlp"] = "otlp"
    OTEL_LOGS_EXPORTER: Literal["otlp"] = "otlp"

    SANDBOX_RUNTIME_CLASS_NAME: str = "gvisor"
    SANDBOX_NAMESPACE: str = "policyflow-sandbox"
    SANDBOX_RESOURCE_POLICY_ID: str = "restricted-v1"
    SANDBOX_NETWORK_POLICY: Literal["deny-all"] = "deny-all"
    SANDBOX_SERVICE_ACCOUNT_AUTOMOUNT: bool = False
    SANDBOX_CPU_LIMIT_MILLICORES: PositiveInt = 1000
    SANDBOX_MEMORY_LIMIT_MIB: PositiveInt = 1024
    SANDBOX_EPHEMERAL_STORAGE_LIMIT_MIB: PositiveInt = 2048
    SANDBOX_PIDS_LIMIT: PositiveInt = 128
    SANDBOX_ACTIVE_DEADLINE_SECONDS: PositiveInt = 300
    SANDBOX_MAX_CONCURRENCY: PositiveInt = 8
    UPLOAD_DIR: Path = Path("uploads")
    RAG_WORKSPACE_DIR: Path = Path("rag_workspaces")
    MAX_UPLOAD_SIZE_MB: PositiveInt = 20
    LIGHTRAG_BASE_URL: str | None = None
    LIGHTRAG_API_KEY: str | None = None
    LIGHTRAG_TIMEOUT_SECONDS: PositiveFloat = 180.0
    # Hybrid reserves the remainder of the Chat turn for answer generation.
    LIGHTRAG_HYBRID_TIMEOUT_SECONDS: PositiveFloat = 45.0
    LIGHTRAG_API_KEY_HEADER: str = "X-API-Key"

    QUOTA_GLOBAL_REQUESTS_PER_MINUTE: PositiveInt = 1200
    QUOTA_TENANT_REQUESTS_PER_MINUTE: PositiveInt = 300
    QUOTA_USER_REQUESTS_PER_MINUTE: PositiveInt = 60
    QUOTA_GLOBAL_MAX_CONCURRENCY: PositiveInt = 100
    QUOTA_TENANT_MAX_CONCURRENCY: PositiveInt = 20
    QUOTA_USER_MAX_CONCURRENCY: PositiveInt = 4
    QUOTA_GLOBAL_LLM_TOKENS_PER_MINUTE: PositiveInt = 1_000_000
    QUOTA_TENANT_LLM_TOKENS_PER_MINUTE: PositiveInt = 200_000
    QUOTA_USER_LLM_TOKENS_PER_MINUTE: PositiveInt = 50_000
    QUOTA_GLOBAL_LLM_BUDGET_USD_CENTS_PER_DAY: PositiveInt = 10_000
    QUOTA_TENANT_LLM_BUDGET_USD_CENTS_PER_DAY: PositiveInt = 2000
    QUOTA_USER_LLM_BUDGET_USD_CENTS_PER_DAY: PositiveInt = 500
    ADMISSION_QUEUE_MAX_ITEMS: PositiveInt = 1000
    ADMISSION_QUEUE_WAIT_TIMEOUT_SECONDS: PositiveFloat = 5.0
    QUOTA_LEASE_DURATION_SECONDS: PositiveInt = 120
    QUOTA_LEASE_HEARTBEAT_SECONDS: PositiveInt = 30
    SSE_CHANNEL_CAPACITY: PositiveInt = 128
    SSE_REPLAY_MAX_EVENTS_PER_RUN: PositiveInt = 1000
    SSE_REPLAY_TTL_SECONDS: PositiveInt = 3600
    SSE_HEARTBEAT_SECONDS: PositiveFloat = 15.0
    SSE_SEND_TIMEOUT_SECONDS: PositiveFloat = 10.0

    LLM_EXECUTION_MODE: Literal["deterministic_mock", "anthropic"] = "deterministic_mock"
    LLM_MOCK_VERSION: str = "policyflow-mock-v1"
    LLM_MOCK_LATENCY_MILLISECONDS: NonNegativeInt = 100
    LLM_MOCK_ERROR_SCRIPT: str = ""
    LLM_MOCK_ARTIFACT_DIR: Path = Path("artifacts/load/mock")
    LLM_REAL_PROVIDER_ARTIFACT_DIR: Path = Path("artifacts/provider/anthropic")

    # The future Claude integration consumes this single provider configuration surface.
    CLAUDE_PROVIDER: Literal["anthropic"] = "anthropic"
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_PROFILE: str | None = None
    CLAUDE_MODEL: Literal["claude-opus-5"] = "claude-opus-5"
    CLAUDE_THINKING_TYPE: Literal["adaptive"] = "adaptive"
    CLAUDE_EFFORT: ClaudeEffort = "high"
    CLAUDE_STREAMING_ENABLED: bool = True
    CLAUDE_TIMEOUT_SECONDS: PositiveFloat = 120.0
    CLAUDE_MAX_OUTPUT_TOKENS: Annotated[int, Field(gt=0, le=128_000)] = 16_000
    CLAUDE_MAX_RETRIES: NonNegativeInt = 2
    CLAUDE_MAX_CONCURRENCY: PositiveInt = 8
    CLAUDE_REQUESTS_PER_MINUTE: PositiveInt = 60
    CLAUDE_INPUT_TOKENS_PER_MINUTE: PositiveInt = 500_000
    CLAUDE_OUTPUT_TOKENS_PER_MINUTE: PositiveInt = 100_000
    CLAUDE_RUN_MAX_REQUESTS: PositiveInt = 16
    CLAUDE_RUN_MAX_TOTAL_TOKENS: PositiveInt = 200_000
    CLAUDE_RUN_BUDGET_USD_CENTS: PositiveInt = 100
    LLM_PROVIDER_NAME: str = "default-openai-compatible"
    LLM_BASE_URL: str | None = None
    LLM_API_KEY_ENV: str = "OPENAI_API_KEY"
    LLM_CHAT_MODEL: str | None = None
    LLM_EMBEDDING_MODEL: str | None = None
    LLM_EMBEDDING_DIM: int = 1536
    LLM_TIMEOUT_SECONDS: float = 120.0
    NVIDIA_RERANKER_API_KEY_ENV: str = "NVIDIA_API_KEY"
    NVIDIA_RERANKER_BASE_URL: str = "https://ai.api.nvidia.com"
    NVIDIA_RERANKER_ENDPOINT_TEMPLATE: str = "/v1/retrieval/{model}/reranking"
    # Keep only models that are still in service upstream. NVIDIA retired
    # llama-nemotron-rerank-1b-v2 and rerank-qa-mistral-4b on 2026-08-25.
    NVIDIA_RERANKER_MODELS: str = "nvidia/llama-nemotron-rerank-vl-1b-v2"
    NVIDIA_RERANKER_TIMEOUT_SECONDS: float = 30.0
    NVIDIA_RERANKER_TRUNCATE: str = "END"
    # Cap concurrent chat/completions against strict providers (e.g. SenseNova 429).
    # Hybrid LightRAG keyword extraction + multi-KB retrieve share this gate.
    LLM_MAX_CONCURRENCY: int = 1
    LLM_MAX_ATTEMPTS: int = 5
    LLM_RETRY_BASE_SECONDS: float = 1.5
    LLM_RETRY_MAX_SECONDS: float = 30.0
    # Cap how many knowledge-base workspaces LightRAG may query in parallel.
    LIGHTRAG_RETRIEVE_MAX_CONCURRENCY: int = 1
    # Memory system (STM/LTM/entity)
    MEMORY_STM_WINDOW_TURNS: int = 6
    MEMORY_LTM_TOP_K: int = 5
    MEMORY_FIXED_PREFS_LIMIT: int = 10
    MEMORY_ENTITY_LIMIT: int = 8
    MEMORY_COMPRESS_TURN_THRESHOLD: int = 8
    MEMORY_LTM_SALIENCE_THRESHOLD: float = 0.55
    MEMORY_WRITEBACK_ENABLED: bool = True
    # LTM recall: relevance × importance × recency (+ access boost). Local formula, not cross-encoder.
    MEMORY_RANK_DECAY_LAMBDA: float = 0.08
    MEMORY_RANK_ACCESS_BOOST_CAP: float = 0.15
    MEMORY_CONVERSATION_FACT_TTL_DAYS: int = 30
    MEMORY_STM_UNLOAD_TTL_DAYS: int = 14
    # Chat grounding: refuse when retrieval returns no evidence (no soft LLM fallback).
    CHAT_HARD_REFUSE_WITHOUT_EVIDENCE: bool = True
    # Route production Chat/Eval through the shared-graph route adapter (T051/T052).
    # ON by default (2026-09-23 cut-over): endpoints go through
    # backend.app.graph.route_adapter.GraphRouteAdapter, which records the Stage 9
    # removal-ledger telemetry and delegates to the shared pipeline graph. Set to
    # False to roll back to the byte-identical legacy direct path (kept until the
    # Stage 9 zero-use window clears — see T053). NOTE: the underlying execution is
    # the pipeline orchestration graph (Option A), not the durable evidence-path
    # GraphService; real-corpus conclusion parity is unverified without the retrieval
    # stack (docs/08 §10, scripts/graph_realcorpus_parity.py).
    ROUTE_VIA_GRAPH_ADAPTER: bool = True
    # Off-topic gate. Preferred signal is the cross-encoder relevance score, which is
    # semantic; the lexical bigram-coverage ratio is only the fallback for retrieval
    # paths that produced no real rerank score (rerank off, or local lexical fusion).
    # NVIDIA rerank scores are logits (observed range ≈ -25…+32), so the threshold is
    # not a 0-1 similarity. Calibrate with scripts/analyze_rerank_scores.py against a
    # run that includes the enterprise negative queries.
    RETRIEVAL_GATE_CROSS_ENCODER_ENABLED: bool = True
    RETRIEVAL_GATE_MIN_CROSS_ENCODER_SCORE: float = -8.0
    RETRIEVAL_GATE_MIN_OVERLAP_RATIO: float = 0.08
    # Answer agent tool-use loop bounds.
    CHAT_TOOL_MAX_ROUNDS: int = 3
    CHAT_TURN_TIMEOUT_SECONDS: float = 180.0
    CHAT_TURN_MAX_LLM_CALLS: int = 16
    # Retrieval budget covers the pipeline retrieval (1, or 2 when the quality
    # gate retries), each PlanExecutor retrieve step, and the answer loop's
    # supplementary kb.search calls. 2 left no room for a supplementary search,
    # so every second kb.search died as a red TURN_BUDGET_EXHAUSTED failure.
    CHAT_TURN_MAX_RETRIEVAL_ATTEMPTS: int = 5
    CHAT_TURN_MAX_TOOL_CALLS: int = 8
    CHAT_TOOL_DEFAULT_TIMEOUT_SECONDS: float = 20.0
    CHAT_ANSWER_REVISE_MAX_ROUNDS: int = 1
    CHAT_TOOLS_ENABLED: bool = True
    # Progressive multi-step planning (Router structured plan; not peer multi-agent).
    CHAT_PLANNING_ENABLED: bool = True
    CHAT_PLAN_MAX_STEPS: int = 5
    # L2: true per-step PlanExecutor (still centralized; not peer multi-agent).
    CHAT_PLAN_EXECUTOR: bool = True
    # Within a ready wave, run independent steps concurrently (e.g. multi-retrieve).
    CHAT_PLAN_PARALLEL: bool = True
    # L2.5 ToT 选路: multi-candidate plans + user pick (not academic ToT search).
    CHAT_TOT_ENABLED: bool = True
    CHAT_TOT_AUTO_TRIGGER: bool = True
    CHAT_TOT_MIN_OPTIONS: int = 2
    CHAT_TOT_MAX_OPTIONS: int = 3
    CHAT_TOT_PENDING_TTL_MINUTES: int = 60
    # Honest LLM reflection closed-loop (Critique → Improve). Not peer multi-agent.
    # Only high-stakes turns; hard max rounds; eval off by default for cost honesty.
    CHAT_REFLECTION_ENABLED: bool = True
    CHAT_REFLECTION_MAX_ROUNDS: int = 2
    CHAT_REFLECTION_IN_EVAL: bool = False
    CHAT_REFLECTION_CONFIDENCE_THRESHOLD: float = 0.72
    CHAT_REFLECTION_PASS_MAX_WARNINGS: int = 1
    CHAT_REFLECTION_ON_MULTI_STEP: bool = True
    CHAT_REFLECTION_ON_RISK: bool = True
    CHAT_REFLECTION_ON_SKILL_SUCCESS: bool = True
    CHAT_REFLECTION_ON_LOW_CONFIDENCE: bool = True

    @field_validator(
        "ENVIRONMENT",
        "CLAUDE_PROVIDER",
        "CLAUDE_MODEL",
        "CLAUDE_THINKING_TYPE",
        "CLAUDE_EFFORT",
        "LLM_EXECUTION_MODE",
        "OBJECT_STORE_PROVIDER",
        mode="before",
    )
    @classmethod
    def normalize_choice(cls, value: object) -> object:
        """Normalize human-entered environment and provider enum values."""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator(
        "UPLOAD_DIR",
        "RAG_WORKSPACE_DIR",
        mode="before",
    )
    @classmethod
    def empty_legacy_path_is_disabled(cls, value: object) -> object:
        """Map blank legacy path settings to the production-disabled sentinel."""
        return _DISABLED_LOCAL_PATH if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "CELERY_BROKER_URL",
        "ANTHROPIC_PROFILE",
        "BOOTSTRAP_ADMIN_PASSWORD",
        "LIGHTRAG_BASE_URL",
        "LIGHTRAG_API_KEY",
        mode="before",
    )
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        """Treat optional, non-secret dotenv placeholders as unset."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "ANTHROPIC_API_KEY",
        "MILVUS_TOKEN",
        "OBJECT_STORE_ACCESS_KEY_ID",
        "OBJECT_STORE_SECRET_ACCESS_KEY",
        "OBJECT_STORE_SESSION_TOKEN",
        "OTEL_EXPORTER_OTLP_HEADERS",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        """Allow intentionally blank secret placeholders without exposing their values."""
        return None if value == "" else value

    @model_validator(mode="after")
    def enforce_configuration_boundaries(self) -> Settings:
        """Fail startup when production authorities or resource bounds are unsafe."""
        if self.CLAUDE_RUN_MAX_REQUESTS > self.CHAT_TURN_MAX_LLM_CALLS:
            raise ValueError(
                "CLAUDE_RUN_MAX_REQUESTS must not exceed CHAT_TURN_MAX_LLM_CALLS"
            )
        if self.CLAUDE_RUN_MAX_TOTAL_TOKENS < self.CLAUDE_MAX_OUTPUT_TOKENS:
            raise ValueError(
                "CLAUDE_RUN_MAX_TOTAL_TOKENS must be at least CLAUDE_MAX_OUTPUT_TOKENS"
            )
        if self.QUOTA_LEASE_HEARTBEAT_SECONDS >= self.QUOTA_LEASE_DURATION_SECONDS:
            raise ValueError(
                "QUOTA_LEASE_HEARTBEAT_SECONDS must be less than QUOTA_LEASE_DURATION_SECONDS"
            )
        if self.ENVIRONMENT is Environment.PRODUCTION:
            self._validate_production()
        return self

    def _validate_production(self) -> None:
        """Enforce the enterprise-only production authority contract."""
        errors: list[str] = []
        database_scheme = _url_scheme(self.DATABASE_URL)
        if "sqlite" in database_scheme or _looks_like_sqlite_url(self.DATABASE_URL):
            errors.append("DATABASE_URL must not use SQLite in production")
        elif database_scheme != "postgresql+psycopg":
            errors.append(
                "DATABASE_URL must use async PostgreSQL via postgresql+psycopg:// in production"
            )

        errors.extend(
            _url_errors(
                "CELERY_BROKER_URL",
                self.CELERY_BROKER_URL,
                allowed_schemes={"amqps"},
                required=True,
            )
        )
        errors.extend(
            _url_errors(
                "REDIS_URL", self.REDIS_URL, allowed_schemes={"rediss"}, required=True
            )
        )
        errors.extend(
            _url_errors(
                "MILVUS_URI", self.MILVUS_URI, allowed_schemes={"https"}, required=True
            )
        )
        errors.extend(
            _url_errors(
                "OBJECT_STORE_ENDPOINT_URL",
                self.OBJECT_STORE_ENDPOINT_URL,
                allowed_schemes={"https"},
                required=True,
            )
        )
        errors.extend(
            _url_errors(
                "OTEL_EXPORTER_OTLP_ENDPOINT",
                self.OTEL_EXPORTER_OTLP_ENDPOINT,
                allowed_schemes={"http", "https"},
                required=True,
            )
        )

        if self.UPLOAD_DIR is not _DISABLED_LOCAL_PATH:
            errors.append(
                "UPLOAD_DIR is a host-local file authority and must be disabled in production"
            )
        if self.RAG_WORKSPACE_DIR is not _DISABLED_LOCAL_PATH:
            errors.append(
                "RAG_WORKSPACE_DIR/LightRAG workspace is a host-local authority and must be "
                "disabled in production"
            )
        if self.LIGHTRAG_BASE_URL:
            errors.append("LIGHTRAG_BASE_URL is a legacy production authority and must be disabled")
        for variable, value in (
            ("LLM_BASE_URL", self.LLM_BASE_URL),
            ("LLM_CHAT_MODEL", self.LLM_CHAT_MODEL),
            ("LLM_EMBEDDING_MODEL", self.LLM_EMBEDDING_MODEL),
        ):
            if value and value.strip():
                errors.append(
                    f"{variable} enables the legacy OpenAI-compatible provider bootstrap and "
                    "must be disabled in production"
                )
        if (
            self.LLM_EXECUTION_MODE == "anthropic"
            and self.ANTHROPIC_API_KEY is None
            and not self.ANTHROPIC_PROFILE
        ):
            errors.append(
                "ANTHROPIC_API_KEY or ANTHROPIC_PROFILE is required when "
                "LLM_EXECUTION_MODE=anthropic in production"
            )
        if not self.MILVUS_TLS_ENABLED:
            errors.append("MILVUS_TLS_ENABLED must be true in production")
        if self.MILVUS_TENANT_PARTITION_KEY != "tenant_id":
            errors.append("MILVUS_TENANT_PARTITION_KEY must be tenant_id in production")
        if not self.OBJECT_STORE_VERSIONING_REQUIRED:
            errors.append("OBJECT_STORE_VERSIONING_REQUIRED must be true in production")
        if not self.OBJECT_STORE_TLS_ENABLED:
            errors.append("OBJECT_STORE_TLS_ENABLED must be true in production")
        if not self.CELERY_TASK_IGNORE_RESULT:
            errors.append("CELERY_TASK_IGNORE_RESULT must be true; RabbitMQ is not result authority")
        if not self.CELERY_TASK_ACKS_LATE:
            errors.append("CELERY_TASK_ACKS_LATE must be true in production")
        if self.RABBITMQ_QUEUE_TYPE != "quorum":
            errors.append("RABBITMQ_QUEUE_TYPE must be quorum in production")
        if self.SANDBOX_RUNTIME_CLASS_NAME.casefold() != "gvisor":
            errors.append("SANDBOX_RUNTIME_CLASS_NAME must be gvisor in production")
        if self.SANDBOX_NETWORK_POLICY != "deny-all":
            errors.append("SANDBOX_NETWORK_POLICY must be deny-all in production")
        if self.SANDBOX_SERVICE_ACCOUNT_AUTOMOUNT:
            errors.append("SANDBOX_SERVICE_ACCOUNT_AUTOMOUNT must be false in production")
        if errors:
            raise ValueError("Production configuration rejected: " + "; ".join(errors))

    @property
    def log_file(self) -> Path:
        return self.LOG_DIR / self.LOG_FILE_NAME


def _url_scheme(value: str) -> str:
    """Return a normalized URL scheme without reflecting credentials."""
    return urlsplit(value.strip()).scheme.casefold()


def _looks_like_sqlite_url(value: str) -> bool:
    """Recognize malformed and async SQLite variants before driver parsing."""
    normalized = value.strip().casefold()
    return normalized.startswith("sqlite") or ":sqlite" in normalized


def _url_errors(
    variable: str,
    value: str | None,
    *,
    allowed_schemes: set[str],
    required: bool,
) -> list[str]:
    """Validate a production service URL without including its secret-bearing value."""
    if value is None or not value.strip():
        return [f"{variable} is required in production"] if required else []
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() not in allowed_schemes:
        choices = ", ".join(f"{scheme}://" for scheme in sorted(allowed_schemes))
        return [f"{variable} must use {choices} in production"]
    if not parsed.hostname:
        return [f"{variable} must include a network host in production"]
    return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
