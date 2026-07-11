"""Tool API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolRead(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    risk_level: str
    enabled: bool
    timeout_seconds: int


class ToolListResponse(BaseModel):
    items: list[ToolRead]


class ToolRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None


class ToolRunResponse(BaseModel):
    name: str
    output: dict[str, Any]
    call_log_id: str
    request_id: str | None


class ToolCallLogRead(BaseModel):
    id: str
    agent_name: str
    tool_name: str
    user_id: str | None
    conversation_id: str | None
    request_id: str | None
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    status: str
    error_message: str | None
    latency_ms: int
    created_at: datetime


class ToolCallLogListResponse(BaseModel):
    items: list[ToolCallLogRead]
    total: int
    page: int
    page_size: int
