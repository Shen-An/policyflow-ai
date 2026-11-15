"""Real API -> durable consumer -> AgentPipeline -> persisted result/approval.

The LLM is deterministic and retrieval uses the actual local BM25 corpus. These
are business-wiring tests, not provider-quality or broker/capacity evidence.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import (
    AgentRun, ApprovalRequest, DurableJob, EvalRun, EvalResult, KnowledgeBase, KnowledgeDocument,
    Material, MaterialVersion, Role, SubmissionJob, UserRole, Department,
    RetrievalEvalItem, Tenant,
)
from backend.app.jobs.consumer import build_worker_runtime, drain_one_cycle
from backend.app.jobs.runner import JobContext, default_registry
from backend.app.jobs.service import JobService
from backend.app.main import create_app
from tests.contract.test_runs_api import (
    ROLE_ALPHA, SECRET, TENANT_ALPHA, USER_ALPHA, _alpha, _create_and_seed,
)


class OfflineLLM:
    available = True
    async def complete(self, system_prompt, user_prompt):
        return "报销制度规定：报销申请需要经理审批，并附票据。[1]"


@pytest.fixture
def business_client(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'business.db').as_posix()}"
    asyncio.run(_create_and_seed(url))
    settings = Settings(DATABASE_URL=url, SECRET_KEY=SECRET, LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads", RAG_WORKSPACE_DIR=tmp_path / "rag",
        BOOTSTRAP_ADMIN_PASSWORD="test-only", MEMORY_WRITEBACK_ENABLED=False, _env_file=None)
    monkeypatch.setattr("backend.app.services.chat_service.get_settings", lambda: settings)
    app = create_app(settings, llm_service=OfflineLLM())
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            role = session.get(Role, ROLE_ALPHA)
            role.code = "sys_admin"
            role.actions = ["approval:decide"]
            session.add(role)
            session.add(UserRole(user_id=USER_ALPHA, role_id=ROLE_ALPHA))
            department = Department(tenant_id=TENANT_ALPHA, code="business", name="Business")
            session.add(department)
            session.flush()
            kb = KnowledgeBase(tenant_id=TENANT_ALPHA, code="business", name="报销制度",
                               department_id=department.id, rag_workspace=str(tmp_path / "rag"))
            session.add(kb)
            session.flush()
            doc = KnowledgeDocument(tenant_id=TENANT_ALPHA, knowledge_base_id=kb.id,
                title="报销制度", file_type="txt", index_status="indexed",
                file_path="test.txt", content_hash="b" * 64, created_by=USER_ALPHA,
                content_text="报销制度规定：报销申请需要经理审批，并附票据。")
            session.add(doc)
            material = Material(tenant_id=TENANT_ALPHA, name="申请材料", source_type="user_upload")
            session.add(material)
            session.flush()
            version = MaterialVersion(tenant_id=TENANT_ALPHA, material_id=material.id,
                                      version_number=1, sha256="a" * 64, status="available",
                                      size_bytes=0, media_type="text/plain", created_by=USER_ALPHA)
            session.add(version)
            session.commit()
            client.kb_id, client.version_id = kb.id, version.id
        yield client


def start(client, kind="chat", key="business-test-0123456789"):
    payload = {"question": "报销制度规定", "knowledge_base_ids": [client.kb_id],
               "retrieval_strategy": "bm25_only"}
    if kind == "file_workflow":
        payload["material_version_ids"] = [client.version_id]
    response = client.post("/api/v2/runs", headers={**_alpha(), "Idempotency-Key": key},
                           json={"kind": kind, "payload": payload})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_chat_runs_pipeline_and_emits_backend_encoded_events(business_client):
    client = business_client
    run_id = start(client)
    view = client.get(f"/api/v2/runs/{run_id}", headers=_alpha())
    assert view.json()["state"] == "succeeded", view.text
    response = client.get(f"/api/v2/runs/{run_id}/events", headers=_alpha())
    assert "event: run.succeeded\r\n" in response.text
    data = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert "报销制度规定" in data[-1]["answer"]
    assert '"evidence_gate": "supported"' in response.text
    resumed = client.get(f"/api/v2/runs/{run_id}/events", headers={**_alpha(), "Last-Event-ID": "d1"})
    assert "id: d1\r\n" not in resumed.text


def test_sse_fixture_matches_actual_backend_encoder():
    from pathlib import Path
    from sse_starlette.sse import ServerSentEvent
    from backend.app.sse.endpoint import _frame
    expected = ServerSentEvent(**_frame("run.succeeded", {"answer": "制度依据", "evidence_gate": "supported"}, "d12")).encode()
    expected += ServerSentEvent(**_frame("run.cancelled", {}, "d13")).encode()
    fixture = Path(__file__).resolve().parents[2] / "frontend/electron/main/fixtures/run-events.sse"
    assert fixture.read_bytes() == expected


def test_future_cursor_recovers_public_snapshot(business_client):
    client = business_client
    run_id = start(client)
    with Session(client.app.state.engine) as session:
        row = session.exec(select(AgentRun).where(AgentRun.run_id == run_id)).one()
        row.result_snapshot = {**row.result_snapshot, "digest_input": {"private": True}, "workspace_id": "internal"}
        session.add(row)
        session.commit()
    stream = client.get(f"/api/v2/runs/{run_id}/events", headers={**_alpha(), "Last-Event-ID": "d99999"}).text
    assert "event: snapshot_required" in stream
    assert "event: run.succeeded" in stream
    assert "digest_input" not in stream
    assert "workspace_id" not in stream


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_file_draft_pauses_then_new_consumer_resumes(business_client, decision):
    client = business_client
    run_id = start(client, "file_workflow")
    response = client.get(f"/api/v2/runs/{run_id}", headers=_alpha())
    assert response.json()["state"] == "waiting_approval", response.text
    with Session(client.app.state.engine) as session:
        approval = session.exec(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)).one()
        assert session.exec(select(SubmissionJob)).all() == []
        approval_id, digest, version = approval.id, approval.action_digest, approval.version
    # Disable the web drain: execute the persisted resume with a fresh consumer,
    # as after a process restart. No HTTP/backend or workflow service is stubbed.
    client.app.state.settings.CELERY_BROKER_URL = "memory://"
    response = client.post(f"/api/v2/runs/{run_id}/approvals/{approval_id}", headers=_alpha(),
        json={"decision": decision, "action_digest": digest, "expected_version": version})
    assert response.status_code == 200, response.text
    async def consume():
        runtime = build_worker_runtime(database_url=client.app.state.settings.DATABASE_URL,
            registry=default_registry(), context=JobContext(engine=client.app.state.engine,
                app_state=client.app.state, lightrag_adapter=client.app.state.lightrag_adapter))
        try:
            assert await drain_one_cycle(runtime) == 1
            assert await drain_one_cycle(runtime) == 0
        finally:
            await runtime.engine.dispose()
    asyncio.run(consume())
    view = client.get(f"/api/v2/runs/{run_id}", headers=_alpha())
    assert view.json()["state"] == ("succeeded" if decision == "approve" else "cancelled"), view.text
    with Session(client.app.state.engine) as session:
        jobs = session.exec(select(SubmissionJob)).all()
        assert len(jobs) == (1 if decision == "approve" else 0)
    events = client.get(f"/api/v2/runs/{run_id}/events", headers=_alpha()).text
    assert ('"status": "mock"' in events) if decision == "approve" else ("run.cancelled" in events)


def test_unknown_kind_and_spoofed_actor_rejected(business_client):
    client = business_client
    headers = {**_alpha(), "Idempotency-Key": "reject-invalid-kind-01"}
    assert client.post("/api/v2/runs", headers=headers, json={"kind": "unknown"}).status_code == 422
    assert client.post("/api/v2/runs", headers=headers,
        json={"kind": "chat", "payload": {"question": "hello", "actor_id": "foreign"}}).status_code == 422


def test_chat_releases_sqlite_writer_before_async_memory(business_client):
    client = business_client
    original_load = client.app.state.memory_agent.load
    observed = []
    async def load_with_other_writer(*args, **kwargs):
        async with client.app.state.async_session_factory() as session:
            from sqlalchemy import update
            await asyncio.wait_for(session.execute(update(Tenant).where(
                Tenant.id == TENANT_ALPHA).values(name="Alpha memory regression")), timeout=2)
            await session.commit()
            observed.append(True)
        return await original_load(*args, **kwargs)
    client.app.state.memory_agent.load = load_with_other_writer
    client.app.state.settings.MEMORY_WRITEBACK_ENABLED = True
    run_id = start(client)
    assert observed == [True]
    assert client.get(f"/api/v2/runs/{run_id}", headers=_alpha()).json()["state"] == "succeeded"


def test_cancelled_approval_cannot_resume_or_mutate_other_run(business_client):
    client = business_client
    run_id = start(client, "file_workflow")
    with Session(client.app.state.engine) as session:
        approval = session.exec(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)).one()
        approval_id, digest = approval.id, approval.action_digest
    body = {"decision": "approve", "action_digest": digest, "expected_version": 1}
    wrong = client.post(f"/api/v2/runs/other-run/approvals/{approval_id}", headers=_alpha(), json=body)
    assert wrong.status_code == 404
    assert client.post(f"/api/v2/runs/{run_id}/cancel", headers=_alpha()).status_code == 200
    assert client.post(f"/api/v2/runs/{run_id}/approvals/{approval_id}", headers=_alpha(), json=body).status_code == 409
    with Session(client.app.state.engine) as session:
        assert session.get(ApprovalRequest, approval_id).status == "pending"
        assert session.exec(select(SubmissionJob)).all() == []


def test_cli_worker_assembles_services_and_runs_bm25_eval(business_client):
    from backend.app.jobs.worker import build_app
    from backend.app.jobs.transport import CELERY_JOB_TASK_NAME
    from backend.app.schemas.eval import EvalRunCreate
    client = business_client
    celery, runtime = build_app(client.app.state.settings)
    assert runtime.context.lightrag_adapter is not None
    assert runtime.context.app_state.agent_pipeline is not None
    with Session(client.app.state.engine) as session:
        doc = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == TENANT_ALPHA)).one()
        item = RetrievalEvalItem(tenant_id=TENANT_ALPHA, query="报销制度规定",
            knowledge_base_ids=[client.kb_id], relevant_document_ids=[doc.id])
        session.add(item)
        session.flush()
        request = EvalRunCreate(name="worker wiring", eval_types=["retrieval"], retrieval_item_ids=[item.id],
            retrieval_config={"strategy": "bm25_only"})
        run = EvalRun(tenant_id=TENANT_ALPHA, name="worker wiring", created_by=USER_ALPHA,
                      config_snapshot=request.model_dump(mode="json"))
        session.add(run)
        session.commit()
        run_id = run.id
    async def consume():
        try:
            service = JobService(factory=runtime.session_factory())
            job = await service.enqueue(tenant_id=TENANT_ALPHA, kind="eval_run",
                payload={"run_id": run_id, "data": request.model_dump(mode="json")}, idempotency_key=run_id)
            assert await drain_one_cycle(runtime) == 1
            assert (await service.get(job.id)).state == "succeeded"
        finally:
            await runtime.engine.dispose()
            await runtime.context.app_state.async_engine.dispose()
            runtime.context.engine.dispose()
            celery.tasks[CELERY_JOB_TASK_NAME]._bridge.close()
    asyncio.run(consume())
    with Session(client.app.state.engine) as session:
        assert session.get(EvalRun, run_id).status == "success"
        results = session.exec(select(EvalResult).where(EvalResult.eval_run_id == run_id)).all()
        assert len(results) == 1
        assert results[0].retrieval_metrics["hit_at_1"] == 1


@pytest.mark.parametrize("kind", ["document_index", "eval_run"])
def test_bare_consumer_fails_missing_dependencies(business_client, kind):
    async def consume():
        runtime = build_worker_runtime(database_url=business_client.app.state.settings.DATABASE_URL,
                                        registry=default_registry())
        service = JobService(factory=async_sessionmaker(runtime.engine, expire_on_commit=False))
        try:
            job = await service.enqueue(tenant_id=TENANT_ALPHA, kind=kind, payload={}, idempotency_key=kind)
            await drain_one_cycle(runtime)
            row = await service.get(job.id)
            assert row.state == "terminal_failed"
            assert row.last_error_code == "MISSING_DEPENDENCY"
        finally:
            await runtime.engine.dispose()
    asyncio.run(consume())
