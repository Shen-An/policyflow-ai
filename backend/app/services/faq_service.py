"""FAQ generation, review, and knowledge-base insertion."""

import hashlib

from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    FAQDraft,
    KnowledgeDocument,
    RagIndexJob,
    User,
    new_id,
    utc_now,
)
from backend.app.schemas.faq import (
    FAQApproveResponse,
    FAQDraftListResponse,
    FAQDraftRead,
    FAQGenerateRequest,
)
from backend.app.services.audit_service import record_audit
from backend.app.services.permission_service import (
    get_document,
    get_knowledge_base,
    require_knowledge_base_permission,
)


def to_faq_read(session: Session, faq: FAQDraft) -> FAQDraftRead:
    knowledge_base = get_knowledge_base(session, faq.knowledge_base_id)
    source_document = (
        session.get(KnowledgeDocument, faq.source_document_id)
        if faq.source_document_id
        else None
    )
    return FAQDraftRead(
        **faq.model_dump(),
        knowledge_base_name=knowledge_base.name,
        source_document_title=source_document.title if source_document else None,
    )


def generate_faq_drafts(
    session: Session,
    user: User,
    data: FAQGenerateRequest,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> FAQDraftListResponse:
    knowledge_base = get_knowledge_base(session, data.knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "admin")
    document = get_document(session, data.source_document_id)
    if document.knowledge_base_id != knowledge_base.id:
        raise ApplicationError("DOCUMENT_NOT_FOUND", "Document is not in the knowledge base", 404)
    source_parts = [
        part.strip()
        for part in (document.content_text or "").replace("！", "。").split("。")
        if part.strip()
    ]
    if not source_parts:
        raise ApplicationError("FAQ_SOURCE_EMPTY", "Source document has no text", 422)
    items = []
    for index in range(data.count):
        answer = source_parts[index % len(source_parts)]
        faq = FAQDraft(
            knowledge_base_id=knowledge_base.id,
            source_document_id=document.id,
            source_conversation_id=data.source_conversation_id,
            question=f"关于《{document.title}》的常见问题 {index + 1}",
            answer=answer,
        )
        session.add(faq)
        session.flush()
        record_audit(
            session,
            action="faq.generate",
            target_type="faq_draft",
            actor_id=user.id,
            target_id=faq.id,
            detail={
                "knowledge_base_id": knowledge_base.id,
                "source_document_id": document.id,
            },
            ip_address=ip_address,
            request_id=request_id,
        )
        items.append(faq)
    session.commit()
    for item in items:
        session.refresh(item)
    return FAQDraftListResponse(items=[to_faq_read(session, item) for item in items])


def list_faq_drafts(
    session: Session,
    user: User,
    knowledge_base_id: str | None = None,
    status: str | None = None,
) -> FAQDraftListResponse:
    statement = select(FAQDraft)
    if knowledge_base_id:
        knowledge_base = get_knowledge_base(session, knowledge_base_id)
        require_knowledge_base_permission(session, user, knowledge_base, "admin")
        statement = statement.where(FAQDraft.knowledge_base_id == knowledge_base_id)
    if status:
        statement = statement.where(FAQDraft.status == status)
    items = session.exec(statement.order_by(col(FAQDraft.created_at).desc())).all()
    return FAQDraftListResponse(items=[to_faq_read(session, item) for item in items])


def approve_faq(
    session: Session,
    user: User,
    faq_id: str,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> FAQApproveResponse:
    faq = session.get(FAQDraft, faq_id)
    if faq is None:
        raise ApplicationError("FAQ_DRAFT_NOT_FOUND", "FAQ draft not found", 404)
    knowledge_base = get_knowledge_base(session, faq.knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "admin")
    if faq.status not in {"draft", "pending_review"}:
        raise ApplicationError("FAQ_STATUS_INVALID", "FAQ draft cannot be approved", 409)
    content = f"问题：{faq.question}{chr(10)}答案：{faq.answer}"
    document_id = new_id()
    document = KnowledgeDocument(
        id=document_id,
        knowledge_base_id=knowledge_base.id,
        title=f"FAQ - {faq.question[:200]}",
        file_path=f"faq/{faq.id}.md",
        file_type="md",
        content_text=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        created_by=user.id,
    )
    job = RagIndexJob(knowledge_document_id=document.id, job_type="insert")
    faq.status = "approved"
    faq.reviewer_id = user.id
    faq.updated_at = utc_now()
    session.add(document)
    session.add(job)
    session.add(faq)
    session.flush()
    record_audit(
        session,
        action="faq.approve",
        target_type="faq_draft",
        actor_id=user.id,
        target_id=faq.id,
        detail={
            "knowledge_base_id": knowledge_base.id,
            "document_id": document.id,
            "index_job_id": job.id,
        },
        ip_address=ip_address,
        request_id=request_id,
    )
    session.commit()
    session.refresh(document)
    session.refresh(job)
    session.refresh(faq)
    return FAQApproveResponse(
        faq_draft=to_faq_read(session, faq),
        document_id=document.id,
        index_job_id=job.id,
    )


def reject_faq(
    session: Session,
    user: User,
    faq_id: str,
    reason: str,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> FAQDraftRead:
    faq = session.get(FAQDraft, faq_id)
    if faq is None:
        raise ApplicationError("FAQ_DRAFT_NOT_FOUND", "FAQ draft not found", 404)
    knowledge_base = get_knowledge_base(session, faq.knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "admin")
    if faq.status not in {"draft", "pending_review"}:
        raise ApplicationError("FAQ_STATUS_INVALID", "FAQ draft cannot be rejected", 409)
    faq.status = "rejected"
    faq.reviewer_id = user.id
    faq.review_note = reason
    faq.updated_at = utc_now()
    session.add(faq)
    record_audit(
        session,
        action="faq.reject",
        target_type="faq_draft",
        actor_id=user.id,
        target_id=faq.id,
        detail={
            "knowledge_base_id": knowledge_base.id,
            "reason": reason[:200],
        },
        ip_address=ip_address,
        request_id=request_id,
    )
    session.commit()
    session.refresh(faq)
    return to_faq_read(session, faq)
