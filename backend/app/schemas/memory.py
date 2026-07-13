"""Memory management API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MemoryItemRead(BaseModel):
    id: str
    owner_type: str
    owner_id: str
    memory_type: str
    content: str
    source: str
    confidence: float
    meta_json: dict[str, Any] = Field(default_factory=dict)
    has_embedding: bool = False
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    items: list[MemoryItemRead]
    total: int
    page: int
    page_size: int
