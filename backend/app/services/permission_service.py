"""Knowledge-base ACL resolution."""

from collections.abc import Collection, Iterable
from typing import Any, Literal, cast

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
    # Keep deleted rows addressable for revive/import paths; callers that need only
    # active KBs should check status themselves (list APIs already filter active).
    return knowledge_base


def get_document(session: Session, document_id: str) -> KnowledgeDocument:
    document = session.get(KnowledgeDocument, document_id)
    if document is None:
        raise ApplicationError("DOCUMENT_NOT_FOUND", "Document not found", 404)
    return document


def subject_pairs_for(user: User, role_ids: Iterable[str]) -> set[tuple[str, str]]:
    """Return every ``(subject_type, subject_id)`` pair that can grant access."""
    pairs: set[tuple[str, str]] = {("user", user.id)}
    pairs.update(("role", role_id) for role_id in role_ids)
    if user.department_id:
        pairs.add(("department", user.department_id))
    return pairs


def resolve_permission_level(
    role_codes: Collection[str],
    subject_pairs: Collection[tuple[str, str]],
    permissions: Iterable[Any],
) -> PermissionLevel | None:
    """Resolve the strongest permission those pairs hold on one knowledge base.

    One implementation, two callers: the synchronous ``Session`` path below and
    the asynchronous unit-of-work path in ``knowledge_base_service``. Extracting
    it is what keeps the ACL from forking into two copies that can disagree.
    """
    if "sys_admin" in role_codes or "kb_admin" in role_codes:
        return "admin"
    allowed = set(subject_pairs)
    matched: list[PermissionLevel] = [
        cast(PermissionLevel, item.permission)
        for item in permissions
        if (item.subject_type, item.subject_id) in allowed
    ]
    if not matched:
        return None
    return max(matched, key=PERMISSION_RANK.__getitem__)


def get_knowledge_base_permission(
    session: Session,
    user: User,
    knowledge_base: KnowledgeBase,
) -> PermissionLevel | None:
    role_links = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
    permissions = session.exec(
        select(KnowledgeBasePermission).where(
            KnowledgeBasePermission.knowledge_base_id == knowledge_base.id
        )
    ).all()
    return resolve_permission_level(
        set(get_user_role_codes(session, user.id)),
        subject_pairs_for(user, [link.role_id for link in role_links]),
        permissions,
    )


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
