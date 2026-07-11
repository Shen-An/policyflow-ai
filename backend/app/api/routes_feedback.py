"""Query-feedback API routes."""

from uuid import UUID

from fastapi import APIRouter, Request

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.schemas.chat import QueryFeedbackCreate, QueryFeedbackRead
from backend.app.services.feedback_service import upsert_query_feedback

router = APIRouter(prefix="/api/query-logs", tags=["feedback"])


@router.post("/{query_log_id}/feedback", response_model=QueryFeedbackRead)
def post_query_feedback(
    query_log_id: UUID,
    data: QueryFeedbackCreate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> QueryFeedbackRead:
    ip_address = request.client.host if request.client is not None else None
    return upsert_query_feedback(
        session,
        user,
        str(query_log_id),
        data,
        ip_address,
        getattr(request.state, "request_id", None),
    )
