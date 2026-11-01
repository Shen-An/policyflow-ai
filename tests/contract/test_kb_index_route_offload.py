"""T035 contract: the re-index endpoint keeps blocking service work off the loop.

``document_service.create_index_job`` is still synchronous (``session: Session``)
and it is not a cheap function: it loads the document and its knowledge base,
checks ``require_knowledge_base_permission``, inserts a ``RagIndexJob``, flips
the document to ``pending``, records an audit row and *commits*. The re-index
endpoint ``POST /api/documents/{id}/index`` is an ``async def``, so calling it
inline parks the event loop for the whole commit -- every other request,
including already-open chat SSE streams, waits behind it.

These tests pin the offload itself, not its effect. The effect (a durable
``job.enqueued`` row plus an ``IndexJobResponse``) also passes when the work
runs inline, so it cannot prove the fix; ``tests/contract/
test_document_index_durable_submission.py`` covers that side.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api import routes_kb
from tests.test_phase1_knowledge import build_knowledge_app, headers, login


def _upload_document(client: TestClient, admin_headers: dict[str, str]) -> str:
    hr_id = client.get("/api/knowledge-bases", headers=admin_headers).json()["items"][
        0
    ]["id"]
    upload = client.post(
        f"/api/knowledge-bases/{hr_id}/documents",
        headers=admin_headers,
        files={"file": ("policy.txt", b"Leave policy body", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    return upload.json()["document_id"]


def test_reindex_service_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    app = build_knowledge_app(tmp_path)
    seen: dict[str, int] = {}

    real_create = routes_kb.create_index_job

    def spy_create(*args, **kwargs):
        seen["service_thread"] = threading.get_ident()
        return real_create(*args, **kwargs)

    real_submit = routes_kb.submit_document_index

    async def spy_submit(*args, **kwargs):
        # Reached from the endpoint body, i.e. on the event-loop thread: this is
        # the thread the blocking service call must *not* occupy.
        seen["event_loop_thread"] = threading.get_ident()
        return await real_submit(*args, **kwargs)

    monkeypatch.setattr(routes_kb, "create_index_job", spy_create)
    monkeypatch.setattr(routes_kb, "submit_document_index", spy_submit)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        document_id = _upload_document(client, admin_headers)
        response = client.post(
            f"/api/documents/{document_id}/index", headers=admin_headers
        )

    assert response.status_code == 200, response.text
    assert set(seen) == {"service_thread", "event_loop_thread"}, seen
    assert seen["service_thread"] != seen["event_loop_thread"], (
        "create_index_job() ran on the event-loop thread; the re-index route must "
        "hand the synchronous service call to starlette's threadpool"
    )


def test_reindex_still_returns_the_job_after_the_offload(tmp_path: Path) -> None:
    """The offload must not change the response the client depends on."""
    app = build_knowledge_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        document_id = _upload_document(client, admin_headers)
        response = client.post(
            f"/api/documents/{document_id}/index", headers=admin_headers
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"]
    assert body["status"] == "pending"
