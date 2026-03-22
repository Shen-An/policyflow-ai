"""Audit-log recording helpers."""

from typing import Any

from sqlmodel import Session

from backend.app.core.logging import get_request_id
from backend.app.db.models import AuditLog


def record_audit(
    session: Session,
    action: str,
    target_type: str,
    actor_id: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail or {},
        ip_address=ip_address,
        request_id=request_id or get_request_id(),
    )
    session.add(audit_log)
    return audit_log
