"""Real desktop business jobs, using the same AgentPipeline as web chat/eval.

AgentRun snapshots persist the file approval checkpoint. Preparation and resume
are separate durable jobs, so a human pause holds no worker lease. The local
sandbox only renders a new fillable Markdown draft; it does not extract receipts
or provide kernel isolation. External submission uses the labelled mock connector.
"""

from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import select, update
from sqlmodel import Session

from backend.app.approvals.digest import ActionDigestInput
from backend.app.approvals.service import ApprovalConflict, ApprovalService, ChangeItemSpec
from backend.app.approvals.submission import SubmissionService
from backend.app.db.models import (
    AgentRun,
    ApprovalRequest,
    ChangeSet,
    DurableJob,
    Material,
    MaterialVersion,
    TaskWorkspace,
    WorkspaceInput,
    utc_now,
)
from backend.app.integrations.mock_submission import MockReimbursementConnector
from backend.app.jobs.runner import BusinessJobResult, JobContext, JobDependencyError
from backend.app.schemas.chat import ChatRequest, ChatResponse
from backend.app.services.chat_service import iter_chat_events


async def actor(ctx: JobContext, user_id: str):
    state = ctx.app_state
    if state is None or not callable(getattr(state, "uow_factory", None)):
        raise JobDependencyError("business jobs require application services")
    async with state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        tenant = await uow.tenants.get_visible(ctx.tenant_id)
        user = await uow.users.get(ctx.tenant_id, user_id)
        grants = await uow.grants.active_grants(ctx.tenant_id, user_id)
        if tenant.status != "active" or user.status != "active" or not grants:
            raise PermissionError("the job actor has no active membership")
        return user


async def ensure_run(ctx: JobContext, payload: dict[str, Any], kind: str) -> AgentRun:
    user = await actor(ctx, str(payload.get("actor_id") or ""))
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        row = await uow.session.scalar(select(AgentRun).where(
            AgentRun.tenant_id == ctx.tenant_id, AgentRun.run_id == ctx.run_id,
        ))
        if row is None:
            row = AgentRun(tenant_id=ctx.tenant_id, user_id=user.id, run_id=ctx.run_id,
                           kind=kind, thread_id=f"desktop:{ctx.run_id}",
                           input_snapshot=payload, status="queued")
            uow.session.add(row)
            await uow.commit()
        elif row.user_id != user.id:
            raise PermissionError("run belongs to another user")
        return row


async def save(ctx: JobContext, *, status: str, snapshot: dict[str, Any] | None = None,
               expected_status: str | None = None,
               milestones: list[tuple[str, dict[str, Any]]] | None = None):
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        row = await uow.session.scalar(select(AgentRun).where(
            AgentRun.tenant_id == ctx.tenant_id, AgentRun.run_id == ctx.run_id,
        ))
        if row is None:
            raise LookupError("business run not found")
        if expected_status is not None and row.status != expected_status:
            raise ApprovalConflict("RUN_STATE_CONFLICT", "run state changed")
        if row.status in {"succeeded", "cancelled", "terminal_failed"} and status != row.status:
            raise ApprovalConflict("RUN_STATE_CONFLICT", "run is already terminal")
        version = row.version
        if snapshot is not None:
            await uow.runs.set_result_snapshot(ctx.tenant_id, ctx.run_id, snapshot, version)
            version += 1
        await uow.runs.set_status(ctx.tenant_id, ctx.run_id, status, version)
        for name, data in milestones or []:
            await uow.run_events.append(ctx.tenant_id, ctx.run_id, name,
                                        public_status=status, payload=data)
        await uow.commit()


async def event(ctx: JobContext, name: str, payload: dict[str, Any], status: str, stage=""):
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        await uow.run_events.append(ctx.tenant_id, ctx.run_id, name,
                                    stage=stage, public_status=status, payload=payload)
        await uow.commit()


async def pipeline_answer(ctx: JobContext, payload: dict[str, Any]) -> ChatResponse:
    user = await actor(ctx, str(payload["actor_id"]))
    data = ChatRequest.model_validate({key: value for key, value in payload.items()
                                      if key in ChatRequest.model_fields})
    final = None
    with Session(ctx.engine) as session:
        async for name, data_payload in iter_chat_events(
            session, user, data, ctx.app_state.agent_pipeline,
            memory_agent=ctx.app_state.memory_agent,
            tool_registry=ctx.app_state.tool_registry,
            skill_registry=ctx.app_state.skill_registry,
            rag_service=ctx.app_state.rag_service,
        ):
            if name == "final":
                final = ChatResponse.model_validate(data_payload)
            elif name == "error":
                raise RuntimeError(str(data_payload.get("code", "CHAT_ERROR")))
            else:
                await event(ctx, "run.progress", data_payload, "running", name)
    if final is None:
        raise RuntimeError("pipeline produced no final response")
    return final


def answer_payload(response: ChatResponse) -> dict[str, Any]:
    return {
        "answer": response.answer,
        "conversation_id": response.conversation_id,
        "message_id": response.message_id,
        "evidence_gate": "supported" if response.citations and response.compliance.decision != "REFUSE"
                         else "insufficient_evidence",
        "citations": [{"title": c.document_title or c.knowledge_base_name,
                       "source": c.knowledge_base_name, "snippet": c.snippet,
                       "document_id": c.document_id} for c in response.citations],
    }


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key in {
        "answer", "conversation_id", "message_id", "evidence_gate", "citations",
        "changeSet", "approval", "submission", "status", "error_code", "rejected",
    }}


async def handle_chat(ctx: JobContext, payload: dict[str, Any]) -> BusinessJobResult:
    row = await ensure_run(ctx, payload, "chat")
    if row.status in {"succeeded", "cancelled"}:
        return BusinessJobResult(ctx.run_id)
    await save(ctx, status="running")
    result = answer_payload(await pipeline_answer(ctx, payload))
    await save(ctx, status="succeeded", snapshot=result, milestones=[("run.succeeded", result)])
    return BusinessJobResult(ctx.run_id)


async def selected_versions(ctx: JobContext, ids: list[str]):
    if not ids:
        raise ValueError("select existing material versions before starting a workflow")
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        selected = []
        for version_id in ids:
            version = await uow.session.scalar(select(MaterialVersion).where(
                MaterialVersion.tenant_id == ctx.tenant_id, MaterialVersion.id == version_id,
            ))
            if version is None or version.status != "available":
                raise ValueError("selected material version is unavailable")
            material = await uow.session.scalar(select(Material).where(
                Material.tenant_id == ctx.tenant_id, Material.id == version.material_id,
            ))
            if material is None:
                raise ValueError("selected material is unavailable")
            selected.append((version, material))
        return selected


async def handle_file_workflow(ctx: JobContext, payload: dict[str, Any]) -> BusinessJobResult:
    row = await ensure_run(ctx, payload, "file_workflow")
    if row.status in {"waiting_approval", "succeeded", "cancelled"}:
        return BusinessJobResult(ctx.run_id)
    selected = await selected_versions(ctx, list(payload.get("material_version_ids", [])))
    await save(ctx, status="running")
    request = {**payload, "question": payload.get("question") or "根据报销制度生成可填写的报销清单"}
    frozen = row.result_snapshot
    result = public_snapshot(frozen) if frozen.get("draft_text") else answer_payload(await pipeline_answer(ctx, request))
    if result["evidence_gate"] != "supported":
        await save(ctx, status="succeeded", snapshot=result, milestones=[("run.succeeded", result)])
        return BusinessJobResult(ctx.run_id)

    from backend.app.core.config import Environment
    from backend.app.sandbox.runner import LocalSandboxBackend, SandboxRequest, SandboxRunner
    from backend.app.sandbox.validation import safe_output_path

    if ctx.app_state.settings.ENVIRONMENT == Environment.PRODUCTION:
        raise JobDependencyError("file draft processor requires a configured production sandbox")
    draft = frozen.get("draft_text") or ("# 报销草稿（待填写）\n\n| 项目 | 填写内容 |\n|---|---|\n"
             "| 申请人 | |\n| 日期 | |\n| 金额 | |\n| 事由 | |\n\n"
             + str(result["answer"]) + "\n\n材料版本：" + ", ".join(v.id for v, _ in selected))
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        workspace = await uow.session.scalar(select(TaskWorkspace).where(
            TaskWorkspace.tenant_id == ctx.tenant_id, TaskWorkspace.run_id == ctx.run_id))
        if workspace is None:
            workspace = TaskWorkspace(tenant_id=ctx.tenant_id, run_id=ctx.run_id,
                                      user_id=row.user_id, session_id="desktop", status="ready")
            uow.session.add(workspace)
            await uow.session.flush()
            for version, material in selected:
                uow.session.add(WorkspaceInput(tenant_id=ctx.tenant_id, workspace_id=workspace.id,
                    material_version_id=version.id, staged_hash=version.sha256,
                    purpose="read", read_only=True))
        await uow.commit()
    await save(ctx, status="running", snapshot={**result, "draft_text": draft, "workspace_id": workspace.id})

    async def render_draft(_input, output):
        safe_output_path(output, "reimbursement.md").write_text(draft, encoding="utf-8")
        return "succeeded"

    async def unused_fetcher(*_args):
        raise ValueError("this processor renders a new template without reading source bytes")

    runner = SandboxRunner(workspace_root=ctx.app_state.settings.UPLOAD_DIR / "workflows",
        backend=LocalSandboxBackend({"reimbursement-fill": render_draft}), fetcher=unused_fetcher)
    sandbox_result = await runner.run_to_completion(SandboxRequest(
        tenant_id=ctx.tenant_id, run_id=ctx.run_id, workspace_id=workspace.id,
        processor_id="reimbursement-fill", inputs=(),
    ))
    if sandbox_result.status != "succeeded" or not sandbox_result.outputs:
        raise RuntimeError("draft processor failed")
    output = sandbox_result.outputs[0]
    diff = "\n".join("+" + line for line in draft.splitlines())
    digest_input = ActionDigestInput(action="reimbursement_submit", destination="mock-expense-system",
        source_versions=[v.id for v, _ in selected], output_versions=[],
        file_hashes={output.relative_path: output.sha256}, diff=diff,
        evidence_set=[str(c["document_id"]) for c in result["citations"] if c["document_id"]],
        permission_snapshot={"tenant_id": ctx.tenant_id, "user_id": row.user_id},
        side_effects=["mock_external_submission"])
    approvals = ApprovalService(factory=ctx.app_state.async_session_factory)
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        existing_change = await uow.session.scalar(select(ChangeSet).where(
            ChangeSet.tenant_id == ctx.tenant_id, ChangeSet.run_id == ctx.run_id))
    change_id = existing_change.id if existing_change else await approvals.create_change_set(
        tenant_id=ctx.tenant_id, workspace_id=workspace.id,
        run_id=ctx.run_id, items=[ChangeItemSpec(operation="create", path=output.relative_path,
                                               after_hash=output.sha256)],
        summary="根据制度生成可填写草稿；材料原件只读", side_effect_class="external_submission")
    expires = utc_now() + timedelta(hours=24)
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        existing_approval = await uow.session.scalar(select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == ctx.tenant_id, ApprovalRequest.change_set_id == change_id))
    if existing_approval:
        approval_id, digest, expires = existing_approval.id, existing_approval.action_digest, existing_approval.expires_at
    else:
        approval_id, digest = await approvals.request_approval(tenant_id=ctx.tenant_id, run_id=ctx.run_id,
            change_set_id=change_id, action=digest_input.action, destination=digest_input.destination,
            requested_by=row.user_id, digest_input=digest_input, expires_at=expires)
    change = {"id": change_id, "state": "ready", "summary": "可填写报销草稿",
              "sideEffectClass": "mock_external_submission", "items": [{"path": output.relative_path,
              "operation": "create", "afterHash": output.sha256, "diff": diff, "preview": draft}]}
    approval = {"approvalId": approval_id, "version": 1, "action": digest_input.action,
        "destination": digest_input.destination, "actionDigest": digest, "status": "pending",
        "expiresAt": expires.isoformat(), "sideEffects": list(digest_input.side_effects),
        "files": [{"path": output.relative_path, "sha256": output.sha256}]}
    checkpoint = {**result, "workspace_id": workspace.id, "changeSet": change, "approval": approval,
                  "digest_input": asdict(digest_input)}
    await save(ctx, status="waiting_approval", snapshot=checkpoint, milestones=[
        ("change_set.ready", {"changeSet": change}), ("approval.requested", {"approval": approval})])
    return BusinessJobResult(ctx.run_id)


async def handle_file_resume(ctx: JobContext, payload: dict[str, Any]) -> BusinessJobResult:
    row = await ensure_run(ctx, payload, "file_workflow")
    if row.status in {"succeeded", "cancelled"}:
        return BusinessJobResult(ctx.run_id)
    snapshot = row.result_snapshot
    approval_id = str(snapshot.get("approval", {}).get("approvalId", ""))
    async with ctx.app_state.uow_factory() as uow:
        await uow.set_tenant_context(ctx.tenant_id)
        approval = await uow.session.get(ApprovalRequest, approval_id)
        if approval is None or approval.tenant_id != ctx.tenant_id or approval.run_id != ctx.run_id:
            raise PermissionError("approval not found for this run")
    if approval.status == "rejected":
        await save(ctx, status="cancelled", snapshot={**snapshot, "rejected": True},
                   milestones=[("run.cancelled", {"rejected": True})])
        return BusinessJobResult(ctx.run_id)
    if approval.status not in {"approved", "consumed"}:
        raise ValueError("approval is not ready to resume")
    await selected_versions(ctx, list(snapshot["digest_input"]["source_versions"]))
    from backend.app.sandbox.validation import safe_output_path
    root = ctx.app_state.settings.UPLOAD_DIR / "workflows" / ctx.tenant_id / snapshot["workspace_id"] / "out"
    for path, digest in snapshot["digest_input"]["file_hashes"].items():
        if sha256(safe_output_path(root, path).read_bytes()).hexdigest() != digest:
            raise ValueError("draft changed since approval")

    async def authority(tenant_id, user_id, _action):
        from backend.app.api.routes_approvals import _APPROVE_ROLES, _APPROVE_SCOPES
        await actor(ctx, user_id)
        async with ctx.app_state.uow_factory() as uow:
            await uow.set_tenant_context(tenant_id)
            grants = await uow.grants.active_grants(tenant_id, user_id)
            for grant in grants:
                role = await uow.roles.get(tenant_id, grant.role_id)
                if role.code in _APPROVE_ROLES or _APPROVE_SCOPES.intersection(role.actions or []):
                    return None
        return "approval authority was revoked"

    service = SubmissionService(factory=ctx.app_state.async_session_factory,
        connector=MockReimbursementConnector(), authority_check=authority)
    outcome = await service.submit_for_approval(tenant_id=ctx.tenant_id, approval_id=approval_id,
        destination=approval.destination, current_digest_input=ActionDigestInput(**snapshot["digest_input"]),
        payload={"draft": snapshot["changeSet"]["items"][0]["preview"]})
    if not outcome.succeeded:
        raise RuntimeError("submission did not succeed")
    result = {"status": outcome.status, "submission": {"status": outcome.status,
        "submissionId": outcome.job_id, "receipt": outcome.receipt_id, "destination": approval.destination}}
    await save(ctx, status="succeeded", snapshot={**snapshot, **result}, milestones=[("run.succeeded", result)])
    return BusinessJobResult(ctx.run_id)


async def enqueue_resume(session, approval: ApprovalRequest):
    """Persist resume intent in the very same transaction as the approval decision."""
    from backend.app.jobs.service import JobService, _digest
    row = await session.scalar(select(AgentRun).where(
        AgentRun.tenant_id == approval.tenant_id, AgentRun.run_id == approval.run_id))
    if row is None or row.status != "waiting_approval":
        raise ApprovalConflict("RUN_STATE_CONFLICT", "run is no longer waiting for approval")
    changed = await session.execute(update(AgentRun).where(
        AgentRun.id == row.id, AgentRun.version == row.version,
        AgentRun.status == "waiting_approval").values(
            status="running", version=row.version + 1))
    if changed.rowcount != 1:
        raise ApprovalConflict("RUN_STATE_CONFLICT", "run state changed")
    job = DurableJob(tenant_id=approval.tenant_id, run_id=approval.run_id,
        kind="file_workflow_resume", idempotency_key=f"approval:{approval.id}",
        payload={"actor_id": approval.requested_by}, payload_digest=_digest({"actor_id": approval.requested_by}))
    session.add(job)
    await session.flush()
    JobService.emit_enqueued(session, job)
