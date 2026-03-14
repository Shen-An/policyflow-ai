"""System-administrator audit-log API routes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.app.api.deps import SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.audit import AuditLogListResponse, AuditLogRead
from backend.app.services.audit_query_service import get_audit_log, list_audit_logs

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])
SysAdminUser = Annotated[User, Depends(require_roles("sys_admin"))]


@router.get("", response_model=AuditLogListResponse)
def get_audit_logs(
    session: SessionDep,
    _: SysAdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    action: str | None = None,
    target_type: str | None = None,
    actor_id: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> AuditLogListResponse:
    return list_audit_logs(
        session,
        page,
        page_size,
        action,
        target_type,
        str(actor_id) if actor_id else None,
        created_from,
        created_to,
    )


@router.get("/{audit_log_id}", response_model=AuditLogRead)
def get_audit_log_route(
    audit_log_id: UUID,
    session: SessionDep,
    _: SysAdminUser,
) -> AuditLogRead:
    return get_audit_log(session, str(audit_log_id))
