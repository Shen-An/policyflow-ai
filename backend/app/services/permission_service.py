"""Knowledge-base ACL resolution."""

from typing import Literal, cast

from sqlmodel import Session, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    KnowledgeBase,
    KnowledgeBasePermission,
    KnowledgeDocument,
    User,
    UserRole,
)
from backend.app.services.user_service import get_user_role_codes

PermissionLevel = Literal["read", "write", "admin"]
PERMISSION_RANK: dict[PermissionLevel, int] = {"read": 1, "write": 2, "admin": 3}


def get_knowledge_base(session: Session, knowledge_base_id: str) -> KnowledgeBase:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ApplicationError("KB_NOT_FOUND", "Knowledge base not found", 404)
    return knowledge_base


def get_document(session: Session, document_id: str) -> KnowledgeDocument:
    document = session.get(KnowledgeDocument, document_id)
    if document is None:
        raise ApplicationError("DOCUMENT_NOT_FOUND", "Document not found", 404)
    return document


def get_knowledge_base_permission(
    session: Session,
    user: User,
    knowledge_base: KnowledgeBase,
) -> PermissionLevel | None:
    role_codes = set(get_user_role_codes(session, user.id))
    if "sys_admin" in role_codes or "kb_admin" in role_codes:
        return "admin"

    role_links = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
    subject_pairs = {("user", user.id)}
    subject_pairs.update(("role", link.role_id) for link in role_links)
    if user.department_id:
        subject_pairs.add(("department", user.department_id))

    permissions = session.exec(
        select(KnowledgeBasePermission).where(
            KnowledgeBasePermission.knowledge_base_id == knowledge_base.id
        )
    ).all()
    matched: list[PermissionLevel] = [
        cast(PermissionLevel, item.permission)
        for item in permissions
        if (item.subject_type, item.subject_id) in subject_pairs
    ]
    if not matched:
        return None
    return max(matched, key=PERMISSION_RANK.__getitem__)


def require_knowledge_base_permission(
    session: Session,
    user: User,
    knowledge_base: KnowledgeBase,
    required_permission: PermissionLevel,
) -> PermissionLevel:
    permission = get_knowledge_base_permission(session, user, knowledge_base)
    if permission is None or PERMISSION_RANK[permission] < PERMISSION_RANK[required_permission]:
        raise ApplicationError(
            "KB_ACCESS_DENIED",
            "Knowledge-base permission denied",
            403,
            {"required_permission": required_permission},
        )
    return permission
