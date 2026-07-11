"""Background document indexing state transitions."""

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, RagIndexJob, utc_now
from backend.app.rag.protocols import DocumentIndexer


async def process_document_index(
    engine: Engine,
    indexer: DocumentIndexer,
    document_id: str,
) -> None:
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None:
            return
        job = session.exec(
            select(RagIndexJob)
            .where(
                RagIndexJob.knowledge_document_id == document.id,
                RagIndexJob.status == "pending",
            )
            .order_by(col(RagIndexJob.created_at).desc())
        ).first()
        knowledge_base = session.get(KnowledgeBase, document.knowledge_base_id)
        if job is None or knowledge_base is None:
            return
        job.status = "running"
        job.started_at = utc_now()
        document.index_status = "indexing"
        document.updated_at = utc_now()
        session.add(job)
        session.add(document)
        session.commit()
        session.refresh(job)
        session.refresh(document)
        session.refresh(knowledge_base)
        job_id = job.id
        detached_document = KnowledgeDocument.model_validate(document.model_dump())
        detached_knowledge_base = KnowledgeBase.model_validate(knowledge_base.model_dump())

    try:
        if not indexer.available:
            raise ApplicationError("LIGHTRAG_UNAVAILABLE", "LightRAG is not configured", 503)
        await indexer.insert_document(detached_knowledge_base, detached_document)
    except Exception as exc:
        with Session(engine) as session:
            failed_document = session.get(KnowledgeDocument, document_id)
            failed_job = session.get(RagIndexJob, job_id)
            if failed_document is not None:
                failed_document.index_status = "failed"
                failed_document.index_error = str(exc)
                failed_document.updated_at = utc_now()
                session.add(failed_document)
            if failed_job is not None:
                failed_job.status = "failed"
                failed_job.error_message = str(exc)
                failed_job.finished_at = utc_now()
                session.add(failed_job)
            session.commit()
        return

    with Session(engine) as session:
        indexed_document = session.get(KnowledgeDocument, document_id)
        completed_job = session.get(RagIndexJob, job_id)
        if indexed_document is not None:
            indexed_document.index_status = "indexed"
            indexed_document.index_error = None
            indexed_document.updated_at = utc_now()
            session.add(indexed_document)
        if completed_job is not None:
            completed_job.status = "success"
            completed_job.finished_at = utc_now()
            session.add(completed_job)
        session.commit()
