"""FAQ draft and review schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class FAQGenerateRequest(BaseModel):
    knowledge_base_id: str
    source_document_id: str
    source_conversation_id: str | None = None
    count: int = Field(default=5, ge=1, le=20)


class FAQDraftRead(BaseModel):
    id: str
    knowledge_base_id: str
    knowledge_base_name: str
    source_document_id: str | None
    source_document_title: str | None
    source_conversation_id: str | None
    question: str
    answer: str
    status: str
    generated_by: str
    reviewer_id: str | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime


class FAQDraftListResponse(BaseModel):
    items: list[FAQDraftRead]


class FAQRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class FAQApproveResponse(BaseModel):
    faq_draft: FAQDraftRead
    document_id: str
    index_job_id: str
