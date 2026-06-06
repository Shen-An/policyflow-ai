"""Knowledge-base and document API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

QueryMode = Literal["naive", "local", "global", "hybrid", "mix"]
PermissionLevel = Literal["read", "write", "admin"]


class DepartmentOption(BaseModel):
    id: str
    code: str
    name: str


class DepartmentListResponse(BaseModel):
    items: list[DepartmentOption]


class KnowledgeBaseCreateOptions(BaseModel):
    departments: list[DepartmentOption]


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9_-]+$")
    department_id: str
    description: str = ""
    default_query_mode: QueryMode = "mix"


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    default_query_mode: QueryMode | None = None
    status: Literal["active", "disabled"] | None = None


class KnowledgeBaseRead(BaseModel):
    id: str
    name: str
    code: str
    department_id: str
    description: str
    rag_workspace: str
    default_query_mode: str
    status: str
    permission: PermissionLevel
    document_count: int


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseRead]
    total: int


class DocumentUploadResponse(BaseModel):
    document_id: str
    title: str
    file_type: str
    index_status: str
    index_job_id: str


class DocumentRead(BaseModel):
    id: str
    title: str
    file_type: str
    index_status: str
    source_version: int
    created_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int
    page_size: int


class IndexJobResponse(BaseModel):
    job_id: str
    status: str


class LatestIndexJob(BaseModel):
    id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None


class DocumentStatusResponse(BaseModel):
    document_id: str
    index_status: str
    index_error: str | None
    latest_job: LatestIndexJob | None


class DocumentDetail(BaseModel):
    id: str
    knowledge_base_id: str
    title: str
    file_type: str
    index_status: str
    index_error: str | None
    source_version: int
    content_text: str
    content_preview: str
    content_length: int
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)


class DocumentDeleteResponse(BaseModel):
    document_id: str
    index_status: str
    deleted: bool = True


class KnowledgeBaseDeleteResponse(BaseModel):
    knowledge_base_id: str
    status: str
    deleted: bool = True
    documents_deleted: int = 0
