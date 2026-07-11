"""Audit-log filtering, actor enrichment, and recursive redaction."""

from datetime import datetime

from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.core.redaction import redact_sensitive
from backend.app.db.models import AuditLog, User
from backend.app.schemas.audit import AuditActor, AuditLogListResponse, AuditLogRead


def _to_audit_read(session: Session, audit_log: AuditLog) -> AuditLogRead:
    user = session.get(User, audit_log.actor_id) if audit_log.actor_id else None
    actor = (
        AuditActor(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
        )
        if user is not None
        else None
    )
    return AuditLogRead(
        id=audit_log.id,
        actor_id=audit_log.actor_id,
        actor=actor,
        action=audit_log.action,
        target_type=audit_log.target_type,
        target_id=audit_log.target_id,
        detail=redact_sensitive(audit_log.detail),
        ip_address=audit_log.ip_address,
        request_id=audit_log.request_id,
        created_at=audit_log.created_at,
    )


def list_audit_logs(
    session: Session,
    page: int,
    page_size: int,
    action: str | None = None,
    target_type: str | None = None,
    actor_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> AuditLogListResponse:
    statement = select(AuditLog)
    if action:
        statement = statement.where(AuditLog.action == action)
    if target_type:
        statement = statement.where(AuditLog.target_type == target_type)
    if actor_id:
        statement = statement.where(AuditLog.actor_id == actor_id)
    if created_from:
        statement = statement.where(AuditLog.created_at >= created_from)
    if created_to:
        statement = statement.where(AuditLog.created_at <= created_to)
    items = session.exec(statement.order_by(col(AuditLog.created_at).desc())).all()
    start = (page - 1) * page_size
    return AuditLogListResponse(
        items=[_to_audit_read(session, item) for item in items[start : start + page_size]],
        total=len(items),
        page=page,
        page_size=page_size,
    )


def get_audit_log(session: Session, audit_log_id: str) -> AuditLogRead:
    audit_log = session.get(AuditLog, audit_log_id)
    if audit_log is None:
        raise ApplicationError("AUDIT_LOG_NOT_FOUND", "Audit log not found", 404)
    return _to_audit_read(session, audit_log)
