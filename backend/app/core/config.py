"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    PROJECT_NAME: str = "PolicyFlow AI"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "sqlite:///./policyflow.db"
    DATABASE_ECHO: bool = False
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = Path("logs")
    LOG_FILE_NAME: str = "policyflow.log"
    BOOTSTRAP_ADMIN_USERNAME: str = "admin"
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@example.com"
    BOOTSTRAP_ADMIN_DISPLAY_NAME: str = "系统管理员"
    BOOTSTRAP_ADMIN_PASSWORD: str | None = None
    UPLOAD_DIR: Path = Path("uploads")
    RAG_WORKSPACE_DIR: Path = Path("rag_workspaces")
    MAX_UPLOAD_SIZE_MB: int = 20
    LIGHTRAG_BASE_URL: str | None = None
    LIGHTRAG_API_KEY: str | None = None
    LIGHTRAG_TIMEOUT_SECONDS: float = 180.0
    LIGHTRAG_API_KEY_HEADER: str = "X-API-Key"
    LLM_PROVIDER_NAME: str = "default-openai-compatible"
    LLM_BASE_URL: str | None = None
    LLM_API_KEY_ENV: str = "OPENAI_API_KEY"
    LLM_CHAT_MODEL: str | None = None
    LLM_EMBEDDING_MODEL: str | None = None
    LLM_EMBEDDING_DIM: int = 1536
    LLM_TIMEOUT_SECONDS: float = 120.0
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
    # Answer agent tool-use loop bounds.
    CHAT_TOOL_MAX_ROUNDS: int = 3
    CHAT_TOOLS_ENABLED: bool = True
    # Progressive multi-step planning (Router structured plan; not peer multi-agent).
    CHAT_PLANNING_ENABLED: bool = True
    CHAT_PLAN_MAX_STEPS: int = 5
    # L2: true per-step PlanExecutor (still centralized; not peer multi-agent).
    CHAT_PLAN_EXECUTOR: bool = True
    # Within a ready wave, run independent steps concurrently (e.g. multi-retrieve).
    CHAT_PLAN_PARALLEL: bool = True

    @property
    def log_file(self) -> Path:
        return self.LOG_DIR / self.LOG_FILE_NAME


@lru_cache
def get_settings() -> Settings:
    return Settings()
