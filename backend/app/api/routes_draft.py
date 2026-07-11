"""Draft lifecycle API routes."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.schemas.draft import (
    DraftCreate,
    DraftExportResponse,
    DraftListResponse,
    DraftRead,
    DraftUpdate,
)
from backend.app.services.draft_service import (
    change_draft_status,
    create_draft,
    export_draft,
    get_draft,
    list_drafts,
    to_draft_read,
    update_draft,
)

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


@router.post("", response_model=DraftRead, status_code=status.HTTP_201_CREATED)
def post_draft(data: DraftCreate, user: CurrentUser, session: SessionDep) -> DraftRead:
    return create_draft(session, user, data)


@router.get("", response_model=DraftListResponse)
def get_drafts(
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: str | None = Query(default=None, alias="status"),
    draft_type: str | None = None,
) -> DraftListResponse:
    return list_drafts(session, user, page, page_size, status_filter, draft_type)


@router.get("/{draft_id}", response_model=DraftRead)
def get_draft_route(draft_id: str, user: CurrentUser, session: SessionDep) -> DraftRead:
    return to_draft_read(get_draft(session, user, draft_id))


@router.put("/{draft_id}", response_model=DraftRead)
def put_draft(
    draft_id: str,
    data: DraftUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> DraftRead:
    return update_draft(session, user, draft_id, data)


@router.post("/{draft_id}/confirm", response_model=DraftRead)
def confirm_draft(draft_id: str, user: CurrentUser, session: SessionDep) -> DraftRead:
    return change_draft_status(session, user, draft_id, "confirmed")


@router.post("/{draft_id}/discard", response_model=DraftRead)
def discard_draft(draft_id: str, user: CurrentUser, session: SessionDep) -> DraftRead:
    return change_draft_status(session, user, draft_id, "discarded")


@router.post("/{draft_id}/export", response_model=DraftExportResponse)
def export_draft_route(
    draft_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> DraftExportResponse:
    return export_draft(session, user, draft_id)
