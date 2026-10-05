"""Background document indexing state transitions.

T086: under the Stage-5 authority the vectors for a document live in Milvus and
are written by :class:`~backend.app.retrieval.indexer.VectorIndexer` as part of
the material saga, with a ``VectorManifest`` recording what exists and a
compare-and-set flag deciding what is retrievable. The LightRAG indexer driven
here is a *migration adapter* over a host-local workspace: it has no manifest, no
version pinning and no reconciliation, so it cannot be an authority for evidence.

Every run of it is counted by
:class:`~backend.app.storage.authority.StorageAuthorityTelemetry`, which is the
Stage 9 deletion condition for this path -- the same mechanism ``graph/compat.py``
uses for the legacy graph adapter. Passing ``telemetry`` is therefore how a
deployment earns the right to delete this code later; omitting it only loses the
count, never changes behaviour.
"""

from sqlalchemy import Update, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, RagIndexJob, utc_now
from backend.app.rag.protocols import DocumentIndexer
from backend.app.storage.authority import (
    StorageAuthorityTelemetry,
    legacy_usage_event,
)


def claim_statement(job_id: str) -> Update:
    """The conditional statement that claims one pending index job.

    Its pending condition is the whole guarantee. Without it the statement would
    overwrite whatever status the row currently carries, which is how two workers
    could both decide they owned the same job and index one document twice.

    It is a named function so a test can execute the very statement the service
    uses, rather than restating it: a test that restates the statement cannot fail
    when the condition is dropped from the service.

    Raises:
        ValueError: when ``job_id`` is empty.
    """
    if not job_id:
        raise ValueError("job_id must be a non-empty string")
    return (
        update(RagIndexJob)
        .where(RagIndexJob.id == job_id, RagIndexJob.status == "pending")
        .values(status="running", started_at=utc_now())
    )


def claim_pending_index_job(session: Session, document_id: str) -> str | None:
    """Claim the newest pending index job of a document, or return None.

    Returns the job id when this caller claimed it, and None when there was nothing
    pending or another worker claimed it first. The status change travels with a
    single conditional statement, so exactly one caller can move a pending job to
    running. Reading the row and then writing it back in a later statement would let
    two workers both read the same pending job, both conclude they owned it, and
    index the same document twice.

    The caller owns the transaction. The claim is committed together with whatever
    else the caller changes, so a job is never left claimed while the state that
    belongs with it is still unset.
    """
    candidate = session.exec(
        select(RagIndexJob)
        .where(
            RagIndexJob.knowledge_document_id == document_id,
            RagIndexJob.status == "pending",
        )
        .order_by(col(RagIndexJob.created_at).desc())
    ).first()
    if candidate is None:
        return None
    claimed = session.execute(claim_statement(str(candidate.id))).rowcount
    if claimed != 1:
        session.rollback()
        return None
    return str(candidate.id)


def pending_index_job_id(session: Session, document_id: str) -> str | None:
    """Return the newest pending index job id of a document without claiming it.

    Read-only companion to :func:`claim_pending_index_job`: the durable-job
    submission path (T070) needs a stable idempotency key per queued index attempt
    (the ``RagIndexJob`` id), but must not move the row to ``running`` -- that
    transition still belongs to :func:`process_document_index` when the job runs.
    Returns None when the document has nothing pending.
    """
    candidate = session.exec(
        select(RagIndexJob)
        .where(
            RagIndexJob.knowledge_document_id == document_id,
            RagIndexJob.status == "pending",
        )
        .order_by(col(RagIndexJob.created_at).desc())
    ).first()
    return str(candidate.id) if candidate is not None else None


async def process_document_index(
    engine: Engine,
    indexer: DocumentIndexer,
    document_id: str,
    *,
    telemetry: StorageAuthorityTelemetry | None = None,
) -> None:
    with Session(engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None:
            return
        knowledge_base = session.get(KnowledgeBase, document.knowledge_base_id)
        if knowledge_base is None:
            return
        job_id = claim_pending_index_job(session, document.id)
        if job_id is None:
            return

        document.index_status = "indexing"
        document.updated_at = utc_now()
        session.add(document)
        session.commit()
        session.refresh(document)
        session.refresh(knowledge_base)
        detached_document = KnowledgeDocument.model_validate(document.model_dump())
        detached_knowledge_base = KnowledgeBase.model_validate(knowledge_base.model_dump())

    try:
        if not indexer.available:
            raise ApplicationError("LIGHTRAG_UNAVAILABLE", "LightRAG is not configured", 503)
        if telemetry is not None:
            # Counted before the call, not after: a run that fails still used the
            # adapter, and the Stage 9 gate asks "was it used", not "did it work".
            telemetry.record(
                legacy_usage_event(
                    adapter="lightrag_index",
                    tenant_id=getattr(detached_document, "tenant_id", None),
                    resource_kind="knowledge_document",
                    resource_id=document_id,
                    reason="document indexed through the host-local LightRAG workspace",
                )
            )
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
