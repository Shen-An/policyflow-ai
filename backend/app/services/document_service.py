"""Knowledge-document upload, listing, and index-job services."""

import hashlib
from pathlib import Path

from fastapi import UploadFile
from sqlmodel import Session, col, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError, ConflictError
from backend.app.db.models import KnowledgeDocument, RagIndexJob, User, new_id, utc_now
from backend.app.rag.document_loader import SUPPORTED_FILE_TYPES, load_document_text
from backend.app.schemas.knowledge import (
    DocumentListResponse,
    DocumentRead,
    DocumentStatusResponse,
    DocumentUploadResponse,
    IndexJobResponse,
    LatestIndexJob,
)
from backend.app.services.audit_service import record_audit
from backend.app.services.permission_service import (
    get_document,
    get_knowledge_base,
    require_knowledge_base_permission,
)


def _file_type(filename: str) -> str:
    file_type = Path(filename).suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_FILE_TYPES:
        raise ApplicationError(
            "DOCUMENT_TYPE_NOT_SUPPORTED",
            "Document type is not supported",
            415,
            {"file_type": file_type or None},
        )
    return file_type


async def upload_document(
    session: Session,
    settings: Settings,
    user: User,
    knowledge_base_id: str,
    upload: UploadFile,
    title: str | None = None,
    ip_address: str | None = None,
) -> DocumentUploadResponse:
    knowledge_base = get_knowledge_base(session, knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "write")

    filename = upload.filename or ""
    file_type = _file_type(filename)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ApplicationError(
            "DOCUMENT_TOO_LARGE",
            "Document exceeds the upload size limit",
            413,
            {"max_size_mb": settings.MAX_UPLOAD_SIZE_MB},
        )
    content_text = load_document_text(content, file_type)
    content_hash = hashlib.sha256(content).hexdigest()
    duplicate = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.content_hash == content_hash,
            KnowledgeDocument.index_status != "deleted",
        )
    ).first()
    if duplicate is not None:
        raise ConflictError(
            "DOCUMENT_DUPLICATE",
            "The same document content already exists in this knowledge base",
            {"document_id": duplicate.id},
        )

    document_title = (title or Path(filename).stem).strip()
    if not document_title or len(document_title) > 255:
        raise ApplicationError("VALIDATION_ERROR", "Document title is invalid", 422)

    document_id = new_id()
    storage_directory = settings.UPLOAD_DIR / knowledge_base.code
    storage_directory.mkdir(parents=True, exist_ok=True)
    file_path = storage_directory / f"{document_id}.{file_type}"
    try:
        file_path.write_bytes(content)
    except OSError as exc:
        raise ApplicationError("DOCUMENT_STORAGE_FAILED", "Document storage failed", 500) from exc

    document = KnowledgeDocument(
        id=document_id,
        knowledge_base_id=knowledge_base.id,
        title=document_title,
        file_path=str(file_path),
        file_type=file_type,
        content_text=content_text,
        content_hash=content_hash,
        created_by=user.id,
    )
    job = RagIndexJob(knowledge_document_id=document.id, job_type="insert")
    session.add(document)
    session.add(job)
    record_audit(
        session,
        action="document.upload",
        target_type="knowledge_document",
        actor_id=user.id,
        target_id=document.id,
        detail={
            "knowledge_base_id": knowledge_base.id,
            "file_type": file_type,
            "content_hash": content_hash,
        },
        ip_address=ip_address,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        file_path.unlink(missing_ok=True)
        raise
    session.refresh(document)
    session.refresh(job)
    return DocumentUploadResponse(
        document_id=document.id,
        title=document.title,
        file_type=document.file_type,
        index_status=document.index_status,
        index_job_id=job.id,
    )


def list_documents(
    session: Session,
    user: User,
    knowledge_base_id: str,
    page: int,
    page_size: int,
) -> DocumentListResponse:
    knowledge_base = get_knowledge_base(session, knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "read")
    documents = session.exec(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.index_status != "deleted",
        )
        .order_by(col(KnowledgeDocument.created_at).desc())
    ).all()
    start = (page - 1) * page_size
    page_items = documents[start : start + page_size]
    return DocumentListResponse(
        items=[
            DocumentRead(
                id=document.id,
                title=document.title,
                file_type=document.file_type,
                index_status=document.index_status,
                source_version=document.source_version,
                created_at=document.created_at,
            )
            for document in page_items
        ],
        total=len(documents),
        page=page,
        page_size=page_size,
    )


def create_index_job(
    session: Session,
    user: User,
    document_id: str,
    ip_address: str | None = None,
) -> IndexJobResponse:
    document = get_document(session, document_id)
    knowledge_base = get_knowledge_base(session, document.knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "write")
    job = RagIndexJob(knowledge_document_id=document.id, job_type="reindex")
    document.index_status = "pending"
    document.index_error = None
    document.updated_at = utc_now()
    session.add(document)
    session.add(job)
    session.flush()
    record_audit(
        session,
        action="document.index_requested",
        target_type="knowledge_document",
        actor_id=user.id,
        target_id=document.id,
        detail={"job_id": job.id},
        ip_address=ip_address,
    )
    session.commit()
    session.refresh(job)
    return IndexJobResponse(job_id=job.id, status=job.status)


def get_document_status(
    session: Session,
    user: User,
    document_id: str,
) -> DocumentStatusResponse:
    document = get_document(session, document_id)
    knowledge_base = get_knowledge_base(session, document.knowledge_base_id)
    require_knowledge_base_permission(session, user, knowledge_base, "read")
    latest_job = session.exec(
        select(RagIndexJob)
        .where(RagIndexJob.knowledge_document_id == document.id)
        .order_by(col(RagIndexJob.created_at).desc())
    ).first()
    latest_job_read = (
        LatestIndexJob(
            id=latest_job.id,
            status=latest_job.status,
            started_at=latest_job.started_at,
            finished_at=latest_job.finished_at,
        )
        if latest_job is not None
        else None
    )
    return DocumentStatusResponse(
        document_id=document.id,
        index_status=document.index_status,
        index_error=document.index_error,
        latest_job=latest_job_read,
    )
