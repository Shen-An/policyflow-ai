"""Knowledge-base creation and ACL-filtered listing."""

from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError, ConflictError
from backend.app.db.models import (
    Department,
    KnowledgeBase,
    KnowledgeBasePermission,
    KnowledgeDocument,
    User,
)
from backend.app.schemas.knowledge import (
    DepartmentListResponse,
    DepartmentOption,
    KnowledgeBaseCreate,
    KnowledgeBaseCreateOptions,
    KnowledgeBaseListResponse,
    KnowledgeBaseRead,
)
from backend.app.services.audit_service import record_audit
from backend.app.services.permission_service import PermissionLevel, get_knowledge_base_permission


def _document_count(session: Session, knowledge_base_id: str) -> int:
    documents = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.index_status != "deleted",
        )
    ).all()
    return len(documents)


def to_knowledge_base_read(
    session: Session,
    knowledge_base: KnowledgeBase,
    permission: PermissionLevel,
) -> KnowledgeBaseRead:
    return KnowledgeBaseRead(
        id=knowledge_base.id,
        name=knowledge_base.name,
        code=knowledge_base.code,
        department_id=knowledge_base.department_id,
        description=knowledge_base.description,
        rag_workspace=knowledge_base.rag_workspace,
        default_query_mode=knowledge_base.default_query_mode,
        status=knowledge_base.status,
        permission=permission,
        document_count=_document_count(session, knowledge_base.id),
    )


def create_knowledge_base(
    session: Session,
    settings: Settings,
    user: User,
    data: KnowledgeBaseCreate,
    ip_address: str | None = None,
) -> KnowledgeBaseRead:
    if session.exec(select(KnowledgeBase).where(KnowledgeBase.code == data.code)).first():
        raise ConflictError("KB_CODE_EXISTS", "Knowledge-base code already exists")
    if session.get(Department, data.department_id) is None:
        raise ApplicationError("DEPARTMENT_NOT_FOUND", "Department not found", 404)

    workspace = settings.RAG_WORKSPACE_DIR / data.code
    Path(workspace).mkdir(parents=True, exist_ok=True)
    knowledge_base = KnowledgeBase(
        name=data.name,
        code=data.code,
        department_id=data.department_id,
        description=data.description,
        rag_workspace=str(workspace),
        default_query_mode=data.default_query_mode,
        created_by=user.id,
    )
    session.add(knowledge_base)
    try:
        session.flush()
        session.add(
            KnowledgeBasePermission(
                knowledge_base_id=knowledge_base.id,
                subject_type="user",
                subject_id=user.id,
                permission="admin",
            )
        )
        session.add(
            KnowledgeBasePermission(
                knowledge_base_id=knowledge_base.id,
                subject_type="department",
                subject_id=data.department_id,
                permission="read",
            )
        )
        record_audit(
            session,
            action="knowledge_base.create",
            target_type="knowledge_base",
            actor_id=user.id,
            target_id=knowledge_base.id,
            detail={"code": data.code, "department_id": data.department_id},
            ip_address=ip_address,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("KB_CODE_EXISTS", "Knowledge-base code already exists") from exc
    session.refresh(knowledge_base)
    return to_knowledge_base_read(session, knowledge_base, "admin")


def list_knowledge_bases(session: Session, user: User) -> KnowledgeBaseListResponse:
    knowledge_bases = session.exec(
        select(KnowledgeBase)
        .where(KnowledgeBase.status == "active")
        .order_by(KnowledgeBase.name)
    ).all()
    items = []
    for knowledge_base in knowledge_bases:
        permission = get_knowledge_base_permission(session, user, knowledge_base)
        if permission is not None:
            items.append(to_knowledge_base_read(session, knowledge_base, permission))
    return KnowledgeBaseListResponse(items=items, total=len(items))


def list_departments(session: Session) -> DepartmentListResponse:
    departments = session.exec(select(Department).order_by(Department.name)).all()
    return DepartmentListResponse(
        items=[
            DepartmentOption(id=department.id, code=department.code, name=department.name)
            for department in departments
        ]
    )


def get_knowledge_base_create_options(session: Session) -> KnowledgeBaseCreateOptions:
    return KnowledgeBaseCreateOptions(departments=list_departments(session).items)


def get_knowledge_base_detail(
    session: Session,
    user: User,
    knowledge_base_id: str,
) -> KnowledgeBaseRead:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ApplicationError("KB_NOT_FOUND", "Knowledge base not found", 404)
    permission = get_knowledge_base_permission(session, user, knowledge_base)
    if permission is None:
        raise ApplicationError("KB_ACCESS_DENIED", "Knowledge-base permission denied", 403)
    return to_knowledge_base_read(session, knowledge_base, permission)
