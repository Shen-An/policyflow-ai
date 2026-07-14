"""Knowledge-base creation and ACL-filtered listing."""

from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError, ConflictError
from shutil import rmtree

from backend.app.db.models import (
    Department,
    FAQDraft,
    KnowledgeBase,
    KnowledgeBasePermission,
    KnowledgeDocument,
    RetrievalEvalItem,
    User,
    utc_now,
)
from backend.app.schemas.knowledge import (
    DepartmentListResponse,
    DepartmentOption,
    KnowledgeBaseCreate,
    KnowledgeBaseCreateOptions,
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseListResponse,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from backend.app.services.audit_service import record_audit
from backend.app.services.permission_service import (
    PermissionLevel,
    get_knowledge_base_permission,
    require_knowledge_base_permission,
)


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
    if knowledge_base is None or knowledge_base.status == "deleted":
        raise ApplicationError("KB_NOT_FOUND", "Knowledge base not found", 404)
    permission = get_knowledge_base_permission(session, user, knowledge_base)
    if permission is None:
        raise ApplicationError("KB_ACCESS_DENIED", "Knowledge-base permission denied", 403)
    return to_knowledge_base_read(session, knowledge_base, permission)


def update_knowledge_base(
    session: Session,
    user: User,
    knowledge_base_id: str,
    data: KnowledgeBaseUpdate,
    ip_address: str | None = None,
) -> KnowledgeBaseRead:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None or knowledge_base.status == "deleted":
        raise ApplicationError("KB_NOT_FOUND", "Knowledge base not found", 404)
    require_knowledge_base_permission(session, user, knowledge_base, "admin")

    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise ApplicationError("VALIDATION_ERROR", "No fields to update", 422)

    if "name" in changes and changes["name"] is not None:
        knowledge_base.name = str(changes["name"]).strip()
    if "description" in changes and changes["description"] is not None:
        knowledge_base.description = str(changes["description"])
    if "default_query_mode" in changes and changes["default_query_mode"] is not None:
        knowledge_base.default_query_mode = str(changes["default_query_mode"])
    if "status" in changes and changes["status"] is not None:
        # Soft lifecycle only; hard delete is a separate endpoint.
        if changes["status"] not in {"active", "disabled"}:
            raise ApplicationError("VALIDATION_ERROR", "Invalid knowledge-base status", 422)
        knowledge_base.status = str(changes["status"])
    knowledge_base.updated_at = utc_now()
    session.add(knowledge_base)
    record_audit(
        session,
        action="knowledge_base.update",
        target_type="knowledge_base",
        actor_id=user.id,
        target_id=knowledge_base.id,
        detail={"changed_fields": sorted(changes.keys()), "code": knowledge_base.code},
        ip_address=ip_address,
    )
    session.commit()
    session.refresh(knowledge_base)
    permission = get_knowledge_base_permission(session, user, knowledge_base) or "admin"
    return to_knowledge_base_read(session, knowledge_base, permission)


def delete_knowledge_base(
    session: Session,
    user: User,
    knowledge_base_id: str,
    ip_address: str | None = None,
) -> KnowledgeBaseDeleteResponse:
    """Physically delete a knowledge base, its documents, ACL, FAQ drafts, and workspace."""
    from backend.app.services.document_service import _purge_document_row

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ApplicationError("KB_NOT_FOUND", "Knowledge base not found", 404)
    require_knowledge_base_permission(session, user, knowledge_base, "admin")

    kb_id = knowledge_base.id
    kb_code = knowledge_base.code
    kb_name = knowledge_base.name
    workspace = knowledge_base.rag_workspace

    documents = session.exec(
        select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb_id)
    ).all()
    deleted_docs = 0
    for document in documents:
        _purge_document_row(session, document)
        deleted_docs += 1

    # FAQ drafts hard-require knowledge_base_id FK.
    faq_items = session.exec(
        select(FAQDraft).where(FAQDraft.knowledge_base_id == kb_id)
    ).all()
    for faq in faq_items:
        session.delete(faq)

    permissions = session.exec(
        select(KnowledgeBasePermission).where(
            KnowledgeBasePermission.knowledge_base_id == kb_id
        )
    ).all()
    for permission in permissions:
        session.delete(permission)

    # Scrub JSON KB references in retrieval eval items.
    eval_items = session.exec(select(RetrievalEvalItem)).all()
    for item in eval_items:
        kb_ids = list(item.knowledge_base_ids or [])
        if kb_id not in kb_ids:
            continue
        remaining = [value for value in kb_ids if value != kb_id]
        if remaining:
            item.knowledge_base_ids = remaining
            session.add(item)
        else:
            session.delete(item)

    record_audit(
        session,
        action="knowledge_base.delete",
        target_type="knowledge_base",
        actor_id=user.id,
        target_id=kb_id,
        detail={
            "code": kb_code,
            "name": kb_name,
            "documents_deleted": deleted_docs,
            "mode": "physical",
        },
        ip_address=ip_address,
    )
    session.delete(knowledge_base)
    session.commit()

    if workspace:
        try:
            rmtree(workspace, ignore_errors=True)
        except OSError:
            pass

    return KnowledgeBaseDeleteResponse(
        knowledge_base_id=kb_id,
        status="deleted",
        deleted=True,
        documents_deleted=deleted_docs,
    )
