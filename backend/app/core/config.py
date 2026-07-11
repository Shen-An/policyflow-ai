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

    @property
    def log_file(self) -> Path:
        return self.LOG_DIR / self.LOG_FILE_NAME


@lru_cache
def get_settings() -> Settings:
    return Settings()
