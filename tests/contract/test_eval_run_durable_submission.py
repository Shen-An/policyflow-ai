"""T070 [US2] route contract: an evaluation run is submitted as a durable job.

``test_phase4_faq_eval`` already proves the end-to-end effect still holds (a
created run reaches ``status == "success"`` after the broker-free drain runs).
This suite proves the *mechanism* the routes_eval slice of T070 introduced: the
long eval run is now a durable :class:`~backend.app.db.models.DurableJob` with a
``job.enqueued`` outbox event -- the durability and the observable event live in
the database, not in an ephemeral FastAPI ``BackgroundTask``. The ``EvalRun`` id
keys idempotency, so a redelivery of the same submit dedups while each freshly
created run enqueues its own durable job. That is the property a restart or a
redelivery relies on, so it is pinned directly rather than inferred from the run
status.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.db.models import DurableJob, OutboxEvent
from backend.app.jobs.runner import EVAL_RUN_KIND
from tests.test_phase4_faq_eval import Phase4LightRAG, build_phase4_app, login


def _create_eval_run(client: TestClient, admin_headers: dict[str, str]) -> str:
    """Seed one indexed doc + case + retrieval item, create a run, return its id."""
    hr_id = next(
        item["id"]
        for item in client.get("/api/knowledge-bases", headers=admin_headers).json()[
            "items"
        ]
        if item["code"] == "hr"
    )
    source_document_id = client.post(
        f"/api/knowledge-bases/{hr_id}/documents",
        headers=admin_headers,
        files={
            "file": (
                "leave.txt",
                b"Annual leave requires manager approval. Submit the request in advance.",
                "text/plain",
            )
        },
        data={"title": "Leave Policy"},
    ).json()["document_id"]
    case_id = client.post(
        "/api/eval/cases",
        headers=admin_headers,
        json={
            "question": "How is annual leave approved?",
            "category": "hr",
            "expected_answer_keywords": ["manager approval"],
            "expected_source_documents": ["Leave Policy"],
            "should_answer": True,
        },
    ).json()["id"]
    retrieval_item_id = client.post(
        "/api/eval/retrieval-items",
        headers=admin_headers,
        json={
            "eval_case_id": case_id,
            "query": "How is annual leave approved?",
            "knowledge_base_ids": [hr_id],
            "relevant_document_ids": [source_document_id],
            "relevant_chunk_ids": [],
        },
    ).json()["id"]
    run_response = client.post(
        "/api/eval/runs",
        headers=admin_headers,
        json={
            "name": "T070 durable eval run",
            "case_ids": [case_id],
            "retrieval_item_ids": [retrieval_item_id],
            "eval_types": ["retrieval"],
            "retrieval_config": {
                "strategy": "lightrag_only",
                "top_k_values": [1, 3, 5],
                "rerank_enabled": False,
                "query_mode": "hybrid",
            },
            "ragas_config": {"enabled": False, "metrics": ["faithfulness"]},
        },
    )
    assert run_response.status_code == 201
    return run_response.json()["id"]


def test_eval_run_is_submitted_as_a_durable_job(tmp_path: Path) -> None:
    app = build_phase4_app(tmp_path, Phase4LightRAG())

    with TestClient(app) as client:
        admin_headers = login(client)
        run_id = _create_eval_run(client, admin_headers)

    # The durable row is the authority. The broker-free drain nudge ran as a
    # BackgroundTask after the response, so the eval-run job reached success.
    with Session(app.state.engine) as session:
        jobs = session.exec(
            select(DurableJob).where(DurableJob.kind == EVAL_RUN_KIND)
        ).all()
        assert len(jobs) == 1
        job = jobs[0]
        # The EvalRun id keys idempotency so a redelivered submit dedups.
        assert job.idempotency_key == run_id
        assert job.payload["run_id"] == run_id
        assert job.state == "succeeded"

        enqueued = session.exec(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == job.id,
                OutboxEvent.event_type == "job.enqueued",
            )
        ).all()
        assert len(enqueued) == 1
