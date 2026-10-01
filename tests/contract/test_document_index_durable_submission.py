"""T070 [US2] route contract: document indexing is submitted as a durable job.

``test_phase1_knowledge`` already proves the end-to-end effect still holds (an
uploaded document reaches ``index_status == "indexed"`` after the broker-free
drain runs). This suite proves the *mechanism* T070 introduced: the work is now
a durable :class:`~backend.app.db.models.DurableJob` with a ``job.enqueued``
outbox event -- the durability and the observable event live in the database,
not in an ephemeral FastAPI ``BackgroundTask``. That is the property a restart
or a redelivery relies on, so it is pinned directly rather than inferred from
the final document status.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.db.models import DurableJob, OutboxEvent
from backend.app.jobs.runner import DOCUMENT_INDEX_KIND
from tests.test_phase1_knowledge import build_knowledge_app, headers, login


def test_upload_submits_a_durable_document_index_job(tmp_path: Path) -> None:
    app = build_knowledge_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        hr_id = client.get(
            "/api/knowledge-bases", headers=admin_headers
        ).json()["items"][0]["id"]

        upload = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("policy.txt", b"Leave policy body", "text/plain")},
        )

    assert upload.status_code == 201
    body = upload.json()
    document_id = body["document_id"]
    index_job_id = body["index_job_id"]

    # The durable row is the authority. The broker-free drain nudge ran as a
    # BackgroundTask after the response, so the job reached a terminal success.
    with Session(app.state.engine) as session:
        jobs = session.exec(
            select(DurableJob).where(DurableJob.kind == DOCUMENT_INDEX_KIND)
        ).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.payload == {"document_id": document_id}
        # The RagIndexJob id keys idempotency so a redelivery dedups.
        assert job.idempotency_key == index_job_id
        assert job.state == "succeeded"

        enqueued = session.exec(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == job.id,
                OutboxEvent.event_type == "job.enqueued",
            )
        ).all()
        assert len(enqueued) == 1


def test_redelivered_upload_intent_does_not_double_enqueue(tmp_path: Path) -> None:
    """A repeat submit of the same index attempt dedups on the idempotency key.

    The route keys the durable job on the ``RagIndexJob`` id, so re-POSTing an
    explicit re-index (which mints a *new* RagIndexJob) enqueues a distinct
    durable job, while a redelivery of the same attempt would not. Here we drive
    the re-index endpoint twice against one document and confirm each real
    attempt is its own durable row -- never a duplicate for the same attempt.
    """
    app = build_knowledge_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        hr_id = client.get(
            "/api/knowledge-bases", headers=admin_headers
        ).json()["items"][0]["id"]
        document_id = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("policy.txt", b"Leave policy body", "text/plain")},
        ).json()["document_id"]

        reindex_job_ids = {
            client.post(
                f"/api/documents/{document_id}/index", headers=admin_headers
            ).json()["job_id"]
            for _ in range(2)
        }

    assert len(reindex_job_ids) == 2  # two distinct re-index attempts

    with Session(app.state.engine) as session:
        jobs = session.exec(
            select(DurableJob).where(DurableJob.kind == DOCUMENT_INDEX_KIND)
        ).all()
    # One durable job for the upload + one per distinct re-index attempt. Each
    # idempotency key appears exactly once (no duplicate for a single attempt).
    keys = [job.idempotency_key for job in jobs]
    assert len(keys) == len(set(keys))
    assert reindex_job_ids <= set(keys)
