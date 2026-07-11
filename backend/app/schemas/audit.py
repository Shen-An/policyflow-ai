"""Audit-log API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditActor(BaseModel):
    id: str
    username: str
    display_name: str


class AuditLogRead(BaseModel):
    id: str
    actor_id: str | None
    actor: AuditActor | None
    action: str
    target_type: str
    target_id: str | None
    detail: dict[str, Any]
    ip_address: str | None
    request_id: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int
