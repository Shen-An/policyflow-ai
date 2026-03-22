"""Draft ownership and lifecycle services."""

from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import Conversation, Draft, User, utc_now
from backend.app.schemas.draft import (
    DraftCreate,
    DraftExportResponse,
    DraftListResponse,
    DraftRead,
    DraftUpdate,
)
from backend.app.services.user_service import get_user_role_codes


def to_draft_read(draft: Draft) -> DraftRead:
    return DraftRead.model_validate(draft.model_dump())


def get_draft(session: Session, user: User, draft_id: str) -> Draft:
    draft = session.get(Draft, draft_id)
    if draft is None:
        raise ApplicationError("DRAFT_NOT_FOUND", "Draft not found", 404)
    if draft.user_id != user.id and "sys_admin" not in get_user_role_codes(session, user.id):
        raise ApplicationError("PERMISSION_DENIED", "Draft access denied", 403)
    return draft


def create_draft(session: Session, user: User, data: DraftCreate) -> DraftRead:
    if data.conversation_id is not None:
        conversation = session.get(Conversation, data.conversation_id)
        if conversation is None:
            raise ApplicationError("CONVERSATION_NOT_FOUND", "Conversation not found", 404)
        if conversation.user_id != user.id and "sys_admin" not in get_user_role_codes(
            session,
            user.id,
        ):
            raise ApplicationError("PERMISSION_DENIED", "Conversation access denied", 403)
    draft = Draft(user_id=user.id, **data.model_dump())
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return to_draft_read(draft)


def list_drafts(
    session: Session,
    user: User,
    page: int,
    page_size: int,
    status: str | None = None,
    draft_type: str | None = None,
) -> DraftListResponse:
    statement = select(Draft)
    if "sys_admin" not in get_user_role_codes(session, user.id):
        statement = statement.where(Draft.user_id == user.id)
    if status:
        statement = statement.where(Draft.status == status)
    if draft_type:
        statement = statement.where(Draft.draft_type == draft_type)
    drafts = session.exec(statement.order_by(col(Draft.created_at).desc())).all()
    start = (page - 1) * page_size
    return DraftListResponse(
        items=[to_draft_read(draft) for draft in drafts[start : start + page_size]],
        total=len(drafts),
        page=page,
        page_size=page_size,
    )


def update_draft(session: Session, user: User, draft_id: str, data: DraftUpdate) -> DraftRead:
    draft = get_draft(session, user, draft_id)
    if draft.status != "draft":
        raise ApplicationError("DRAFT_NOT_EDITABLE", "Only draft items can be edited", 409)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(draft, field, value)
    draft.updated_at = utc_now()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return to_draft_read(draft)


def change_draft_status(
    session: Session,
    user: User,
    draft_id: str,
    target_status: str,
) -> DraftRead:
    draft = get_draft(session, user, draft_id)
    if draft.status not in {"draft", "confirmed"}:
        raise ApplicationError("DRAFT_STATUS_INVALID", "Draft status transition is invalid", 409)
    draft.status = target_status
    draft.updated_at = utc_now()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return to_draft_read(draft)


def export_draft(session: Session, user: User, draft_id: str) -> DraftExportResponse:
    draft = get_draft(session, user, draft_id)
    draft.status = "exported"
    draft.updated_at = utc_now()
    session.add(draft)
    session.commit()
    content = f"# {draft.title}{chr(10)}{chr(10)}{draft.content}"
    return DraftExportResponse(content=content)
