"""Isolated review probes; assertions describe observed defects, not desired behavior.

Run from the repository root with the policyflow Python environment. All database
writes go to a new temporary directory. No broker, LLM, or business database is used.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from sqlmodel import Session

from backend.app.core.config import Settings
from backend.app.db.models import EvalCase, RetrievalEvalItem, Role, UserRole
from backend.app.db.session import configure_async_event_loop_policy
from backend.app.jobs.consumer import build_worker_runtime, drain_one_cycle
from backend.app.jobs.runner import default_registry
from backend.app.jobs.service import JobService
from backend.app.main import create_app
from tests.contract.test_runs_api import (
    ROLE_ALPHA,
    SECRET,
    TENANT_ALPHA,
    TENANT_BETA,
    USER_ALPHA,
    _alpha,
    _create_and_seed,
)


async def consume(url: str, queued: list[dict]) -> list[dict]:
    # Same default context construction as jobs.worker.build_app; no injectable
    # application services are supplied by that production entrypoint either.
    runtime = build_worker_runtime(database_url=url, registry=default_registry())
    service = JobService(factory=runtime.session_factory())
    observations = []
    try:
        for _ in queued:
            await drain_one_cycle(runtime)
        for entry in queued:
            job = await service.get(entry["job_id"])
            observations.append({
                "kind": job.kind,
                "state": job.state,
                "error": job.last_error_code,
            })
        return observations
    finally:
        await runtime.engine.dispose()


def main() -> None:
    configure_async_event_loop_policy()
    with TemporaryDirectory(prefix="policyflow-review-") as scratch:
        root = Path(scratch)
        url = f"sqlite:///{(root / 'review.db').as_posix()}"
        asyncio.run(_create_and_seed(url))
        settings = Settings(
            DATABASE_URL=url,
            LOG_DIR=root / "logs",
            UPLOAD_DIR=root / "uploads",
            RAG_WORKSPACE_DIR=root / "rag",
            SECRET_KEY=SECRET,
            BOOTSTRAP_ADMIN_PASSWORD="review-isolated-only",
            _env_file=None,
        )
        app = create_app(settings)
        with TestClient(app) as client:
            with Session(app.state.engine) as session:
                role = session.get(Role, ROLE_ALPHA)
                role.code = "kb_admin"
                session.add(role)
                session.add(UserRole(user_id=USER_ALPHA, role_id=ROLE_ALPHA))
                foreign_case = EvalCase(
                    tenant_id=TENANT_BETA, question="Tenant B only", category="hr"
                )
                foreign_item = RetrievalEvalItem(
                    tenant_id=TENANT_BETA, query="Tenant B stale gold"
                )
                session.add(foreign_case)
                session.add(foreign_item)
                session.commit()
                case_id, item_id = foreign_case.id, foreign_item.id

            response = client.post("/api/eval/datasets/cleanup", headers=_alpha(), json={})
            assert response.status_code == 200, response.text
            with Session(app.state.engine) as session:
                result = {
                    "caller_tenant": TENANT_ALPHA,
                    "affected_tenant": TENANT_BETA,
                    "http_status": response.status_code,
                    "foreign_case_enabled": session.get(EvalCase, case_id).enabled,
                    "foreign_retrieval_item_deleted": session.get(RetrievalEvalItem, item_id) is None,
                }
            assert result["foreign_case_enabled"] is False
            assert result["foreign_retrieval_item_deleted"] is True
            print("CROSS_TENANT_CLEANUP=" + json.dumps(result))

            queued = []
            for kind in ("chat", "file_workflow", "document_index", "eval_run"):
                response = client.post(
                    "/api/v2/runs",
                    headers={**_alpha(), "Idempotency-Key": "review-isolated-" + kind},
                    json={"kind": kind, "payload": {}},
                )
                assert response.status_code == 201, response.text
                queued.append(response.json())
            outcomes = asyncio.run(consume(url, queued))
            print("DEFAULT_WORKER=" + json.dumps(outcomes))
            by_kind = {row["kind"]: row for row in outcomes}
            for kind in ("chat", "file_workflow"):
                assert by_kind[kind]["state"] == "terminal_failed"
                assert by_kind[kind]["error"] == "NO_HANDLER"
            for kind in ("document_index", "eval_run"):
                # Empty input would fail if the business handler actually ran.
                assert by_kind[kind]["state"] == "succeeded"
        print("All three review findings reproduced in an isolated SQLite database.")
        logging.shutdown()


if __name__ == "__main__":
    main()
