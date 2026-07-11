"""Draft API schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DraftType = Literal["email", "checklist", "application", "faq", "help_request", "summary"]


class DraftCreate(BaseModel):
    conversation_id: str | None = None
    draft_type: DraftType
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    source_question: str = ""
    related_sources: list[dict[str, Any]] = Field(default_factory=list)


class DraftUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1)


class DraftRead(BaseModel):
    id: str
    user_id: str
    conversation_id: str | None
    draft_type: str
    title: str
    content: str
    source_question: str
    related_sources: list[dict[str, Any]]
    status: str
    created_at: datetime
    updated_at: datetime


class DraftListResponse(BaseModel):
    items: list[DraftRead]
    total: int
    page: int
    page_size: int


class DraftExportResponse(BaseModel):
    export_type: str = "markdown"
    content: str
