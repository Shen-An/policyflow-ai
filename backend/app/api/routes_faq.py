"""FAQ generation and review API routes."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from backend.app.api.deps import SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.faq import (
    FAQApproveResponse,
    FAQDraftListResponse,
    FAQDraftRead,
    FAQGenerateRequest,
    FAQRejectRequest,
)
from backend.app.services.faq_service import (
    approve_faq,
    generate_faq_drafts,
    list_faq_drafts,
    reject_faq,
)
from backend.app.services.indexing_service import process_document_index

router = APIRouter(prefix="/api/faq-drafts", tags=["faq"])
KnowledgeAdmin = Annotated[User, Depends(require_roles("kb_admin", "sys_admin"))]


@router.post("", response_model=FAQDraftListResponse, status_code=status.HTTP_201_CREATED)
def post_faq_drafts(
    data: FAQGenerateRequest,
    request: Request,
    user: KnowledgeAdmin,
    session: SessionDep,
) -> FAQDraftListResponse:
    ip_address = request.client.host if request.client is not None else None
    return generate_faq_drafts(
        session,
        user,
        data,
        ip_address,
        getattr(request.state, "request_id", None),
    )


@router.get("", response_model=FAQDraftListResponse)
def get_faq_drafts(
    user: KnowledgeAdmin,
    session: SessionDep,
    knowledge_base_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
) -> FAQDraftListResponse:
    return list_faq_drafts(session, user, knowledge_base_id, status_filter)


@router.post("/{faq_id}/approve", response_model=FAQApproveResponse)
def post_faq_approve(
    faq_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: KnowledgeAdmin,
    session: SessionDep,
) -> FAQApproveResponse:
    ip_address = request.client.host if request.client is not None else None
    response = approve_faq(
        session,
        user,
        faq_id,
        ip_address,
        getattr(request.state, "request_id", None),
    )
    background_tasks.add_task(
        process_document_index,
        request.app.state.engine,
        request.app.state.lightrag_adapter,
        response.document_id,
    )
    return response


@router.post("/{faq_id}/reject", response_model=FAQDraftRead)
def post_faq_reject(
    faq_id: str,
    data: FAQRejectRequest,
    request: Request,
    user: KnowledgeAdmin,
    session: SessionDep,
) -> FAQDraftRead:
    ip_address = request.client.host if request.client is not None else None
    return reject_faq(
        session,
        user,
        faq_id,
        data.reason,
        ip_address,
        getattr(request.state, "request_id", None),
    )
