"""Cross-instance behaviour of the index job claim.

The claim has to hold when two instances run at once, so the property under test is
about two overlapping database sessions, not about one process calling a function
twice. A sequential test cannot see the difference: after the first claim commits,
a second read finds nothing pending whether or not the claim is conditional, which
is exactly what the code before the fix also did. Only an interleaving in which
both sessions read the same pending row before either writes can tell the two apart.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from backend.app.db.models import (
    LEGACY_TENANT_ID,
    Department,
    KnowledgeBase,
    KnowledgeDocument,
    RagIndexJob,
)
from backend.app.services.indexing_service import claim_statement
from tests import conftest


@pytest.fixture(scope="module")
def claim_url(pg_url: str) -> Iterator[str]:
    """A migrated PostgreSQL database with the legacy tenant already adopted."""
    with conftest.scratch_database(pg_url, "pf_it_claim") as url:
        conftest.alembic_upgrade(url, "001")
        conftest.run_legacy_backfill(url)
        conftest.alembic_upgrade(url, "head")
        yield url


def _set_tenant(session: Session, tenant_id: str) -> None:
    """Declare the tenant on the connection, as the application does per request."""
    session.execute(
        text("SELECT set_config('policyflow.tenant_id', :tenant, false)"),
        {"tenant": tenant_id},
    )


def _pending_index_job(url: str) -> tuple[str, str]:
    """Create one pending index job and return its id with its document id.

    The claim_url database is module-scoped, so every call must seed rows with
    distinct natural keys; a fixed department/base code collides on the second
    call under ``ix_departments_code``.
    """
    suffix = uuid4().hex[:8]
    engine = create_engine(url)
    try:
        with Session(engine) as setup:
            _set_tenant(setup, LEGACY_TENANT_ID)
            department = Department(name="Claim Test", code=f"claim-test-department-{suffix}")
            setup.add(department)
            setup.flush()
            knowledge_base = KnowledgeBase(
                tenant_id=LEGACY_TENANT_ID,
                name="Claim Test",
                code=f"claim-test-base-{suffix}",
                department_id=department.id,
                rag_workspace=f"claim-test-workspace-{suffix}",
            )
            setup.add(knowledge_base)
            setup.flush()
            document = KnowledgeDocument(
                tenant_id=LEGACY_TENANT_ID,
                knowledge_base_id=knowledge_base.id,
                title="claim test document",
                file_path=f"claim-test-{suffix}.md",
                file_type="md",
                content_hash=f"claim-test-content-hash-{suffix}",
                created_by="system",
            )
            setup.add(document)
            setup.flush()
            job = RagIndexJob(knowledge_document_id=document.id)
            setup.add(job)
            setup.commit()
            return str(job.id), str(document.id)
    finally:
        engine.dispose()


def test_two_workers_reading_the_same_pending_job_cannot_both_claim_it(
    claim_url: str,
) -> None:
    """Only the first of two overlapping claims changes a row.

    The statement executed here is the one the service executes, imported rather
    than restated, so dropping the pending condition from the service fails this
    test. The two sessions both read the job while it is still pending, which is the
    window the previous read-then-write claim left open.
    """
    job_id, document_id = _pending_index_job(claim_url)
    pending = select(RagIndexJob).where(
        RagIndexJob.knowledge_document_id == document_id,
        RagIndexJob.status == "pending",
    )
    engine = create_engine(claim_url)
    try:
        with Session(engine) as first, Session(engine) as second:
            _set_tenant(first, LEGACY_TENANT_ID)
            _set_tenant(second, LEGACY_TENANT_ID)

            seen_by_first = first.exec(pending).first()
            seen_by_second = second.exec(pending).first()
            assert seen_by_first is not None and seen_by_second is not None
            assert seen_by_first.id == seen_by_second.id == job_id

            assert first.execute(claim_statement(job_id)).rowcount == 1
            first.commit()
            assert second.execute(claim_statement(job_id)).rowcount == 0
            second.rollback()

        with Session(engine) as verify:
            _set_tenant(verify, LEGACY_TENANT_ID)
            job = verify.get(RagIndexJob, job_id)
            assert job is not None
            assert job.status == "running"
            assert job.started_at is not None
    finally:
        engine.dispose()


def test_claiming_nothing_pending_changes_nothing(claim_url: str) -> None:
    """A claim against an already running job is refused rather than repeated."""
    job_id, _ = _pending_index_job(claim_url)
    engine = create_engine(claim_url)
    try:
        with Session(engine) as session:
            _set_tenant(session, LEGACY_TENANT_ID)
            assert session.execute(claim_statement(job_id)).rowcount == 1
            session.commit()
            assert session.execute(claim_statement(job_id)).rowcount == 0
            session.rollback()
    finally:
        engine.dispose()


def test_an_empty_job_id_is_refused() -> None:
    """An empty identifier is a programming error, not a missing row."""
    with pytest.raises(ValueError):
        claim_statement("")
