"""Query-feedback ownership, upsert, and audit services."""

from sqlmodel import Session, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import AIQueryLog, QueryFeedback, User, utc_now
from backend.app.schemas.chat import QueryFeedbackCreate, QueryFeedbackRead
from backend.app.services.audit_service import record_audit
from backend.app.services.user_service import get_user_role_codes


def upsert_query_feedback(
    session: Session,
    user: User,
    query_log_id: str,
    data: QueryFeedbackCreate,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> QueryFeedbackRead:
    query_log = session.get(AIQueryLog, query_log_id)
    if query_log is None:
        raise ApplicationError("QUERY_LOG_NOT_FOUND", "Query log not found", 404)
    if query_log.user_id != user.id and "sys_admin" not in get_user_role_codes(session, user.id):
        raise ApplicationError("PERMISSION_DENIED", "Query feedback access denied", 403)

    feedback = session.exec(
        select(QueryFeedback).where(
            QueryFeedback.query_log_id == query_log.id,
            QueryFeedback.user_id == user.id,
        )
    ).first()
    action = "query_feedback.update" if feedback is not None else "query_feedback.create"
    if feedback is None:
        feedback = QueryFeedback(
            query_log_id=query_log.id,
            user_id=user.id,
            rating=data.rating,
            comment=data.comment,
        )
    else:
        feedback.rating = data.rating
        feedback.comment = data.comment
        feedback.updated_at = utc_now()

    session.add(feedback)
    record_audit(
        session,
        action=action,
        target_type="ai_query_log",
        actor_id=user.id,
        target_id=query_log.id,
        detail={
            "rating": data.rating,
            "comment_present": data.comment is not None,
        },
        ip_address=ip_address,
        request_id=request_id,
    )
    session.commit()
    session.refresh(feedback)
    return QueryFeedbackRead.model_validate(feedback.model_dump())
