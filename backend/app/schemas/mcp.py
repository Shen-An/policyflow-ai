"""MCP mock API schemas."""

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["mock", "external"] = "mock"
    integration_mode: Literal["mock", "stdio", "http"] = "mock"
    endpoint: str | None = Field(default=None, max_length=500)
    command: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] = Field(default_factory=lambda: {"mode": "mock"})
    enabled: bool = False

    @model_validator(mode="after")
    def validate_integration(self) -> "MCPServerCreate":
        if self.integration_mode == "mock":
            if self.type != "mock":
                raise ValueError("mock integration_mode requires type=mock")
            return self
        if self.type != "external":
            raise ValueError("external integration modes require type=external")
        if self.integration_mode == "stdio" and not self.command:
            raise ValueError("stdio integration requires command")
        if self.integration_mode == "http":
            if not self.endpoint:
                raise ValueError("http integration requires endpoint")
            parsed = urlparse(self.endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("endpoint must be a credential-free HTTP(S) base URL")
        return self


class MCPServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    type: Literal["mock", "external"] | None = None
    integration_mode: Literal["mock", "stdio", "http"] | None = None
    endpoint: str | None = Field(default=None, max_length=500)
    command: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class MCPServerRead(BaseModel):
    id: str
    name: str
    type: str
    integration_mode: str
    endpoint: str | None
    command_configured: bool
    config_summary: dict[str, Any]
    enabled: bool
    health_status: str
    tools: list[str]
    last_error_code: str | None
    last_error_message: str | None
    last_checked_at: datetime | None


class MCPServerListResponse(BaseModel):
    items: list[MCPServerRead]


class MCPHealthResponse(BaseModel):
    server_id: str
    health_status: str
    tools: list[str]
    checked_at: datetime
    error_code: str | None
    error_message: str | None
