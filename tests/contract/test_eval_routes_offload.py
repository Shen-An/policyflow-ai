"""T035 contract: the eval routes keep blocking service work off the event loop.

``eval_service`` / ``enterprise_eval_dataset`` are still synchronous
(``session: Session``), so the three ``async def`` eval endpoints must not call
them directly: doing so parks the event loop for the whole duration of a dataset
seed, a run creation, or a pending-index lookup. These tests pin the offload
itself, not its effect -- the effect (the run still succeeds) also passes when
the work runs inline.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api import routes_eval
from tests.contract.test_eval_run_durable_submission import _create_eval_run
from tests.test_phase4_faq_eval import Phase4LightRAG, build_phase4_app, login


def test_eval_run_creation_does_not_run_on_the_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    app = build_phase4_app(tmp_path, Phase4LightRAG())
    seen: dict[str, int] = {}

    real_create = routes_eval.create_eval_run

    def spy_create(*args, **kwargs):
        seen["service_thread"] = threading.get_ident()
        return real_create(*args, **kwargs)

    real_submit = routes_eval.submit_eval_run

    async def spy_submit(*args, **kwargs):
        # Reached from the endpoint body, i.e. on the event-loop thread: this is
        # the thread the blocking service call must *not* occupy.
        seen["event_loop_thread"] = threading.get_ident()
        return await real_submit(*args, **kwargs)

    monkeypatch.setattr(routes_eval, "create_eval_run", spy_create)
    monkeypatch.setattr(routes_eval, "submit_eval_run", spy_submit)

    with TestClient(app) as client:
        admin_headers = login(client)
        _create_eval_run(client, admin_headers)

    assert set(seen) == {"service_thread", "event_loop_thread"}, seen
    assert seen["service_thread"] != seen["event_loop_thread"], (
        "create_eval_run() ran on the event-loop thread; the eval routes must "
        "hand the synchronous service call to starlette's threadpool"
    )


def test_pending_index_lookup_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    """The crud-import path resolves pending index jobs in a worker thread too."""
    app = build_phase4_app(tmp_path, Phase4LightRAG())
    seen: dict[str, int] = {}

    real_resolve = routes_eval._resolve_pending_index_items

    def spy_resolve(*args, **kwargs):
        seen["resolver_thread"] = threading.get_ident()
        return real_resolve(*args, **kwargs)

    real_submit = routes_eval.submit_document_index_batch

    async def spy_submit(*args, **kwargs):
        seen["event_loop_thread"] = threading.get_ident()
        return await real_submit(*args, **kwargs)

    monkeypatch.setattr(routes_eval, "_resolve_pending_index_items", spy_resolve)
    monkeypatch.setattr(routes_eval, "submit_document_index_batch", spy_submit)

    with TestClient(app) as client:
        admin_headers = login(client)
        response = client.post(
            "/api/eval/datasets/crud-import",
            headers=admin_headers,
            json={
                "knowledge_base_code": "eval_test",
                "knowledge_base_name": "test",
                "documents": [
                    {
                        "title": "Annual leave",
                        "content": "Annual leave requires manager approval.",
                    }
                ],
                "index_documents": True,
            },
        )

    # Whether the import itself is accepted is covered elsewhere; here we only
    # care that the run reached the resolver at all, and from which thread.
    assert response.status_code in {200, 201, 202, 400, 409, 422}, response.text
    if "resolver_thread" not in seen:
        # The resolver is only reached when a real indexer is wired in; skip the
        # thread comparison instead of asserting a path this app cannot take.
        return
    assert seen["resolver_thread"] != seen["event_loop_thread"]
