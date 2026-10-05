"""T107 [US1] workspace + change/preview/submission query surface.

This is the read/select surface the UI drives. Two rules shape every endpoint:

* **IDs in, IDs out.** A caller selects material *versions* by id; it never names
  a bucket, an object key, a host path or a sandbox reference, and the responses
  never contain any of those either. The serializers whitelist safe fields
  explicitly rather than dumping a row, so an added column cannot silently start
  leaking infrastructure detail.
* **Authorization is tenant-scoped and version-checked.** A workspace can only
  select material versions that belong to the caller's tenant and are available
  immutable versions; a formal policy original is pulled in read-only. Selecting
  an unknown or foreign version is a 404, indistinguishable from absence.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.api.deps import PrincipalDep
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    ApprovalRequest,
    ChangeSet,
    ChangeSetItem,
    Material,
    MaterialVersion,
    SubmissionJob,
    TaskWorkspace,
    WorkspaceInput,
)

router = APIRouter(prefix="/api/v2", tags=["v2", "workspaces"])


class WorkspaceCreateRequest(BaseModel):
    """Create a workspace by selecting material versions. IDs only."""

    run_id: str = Field(min_length=1, max_length=36)
    material_version_ids: list[str] = Field(min_length=1, max_length=50)
    session_id: str = Field(default="", max_length=64)


def _factory(request: Request) -> async_sessionmaker:
    override = getattr(request.app.state, "workspace_factory", None)
    if override is not None:
        return override
    return async_sessionmaker(request.app.state.async_engine, expire_on_commit=False)


def _workspace_view(workspace: TaskWorkspace, inputs: list[WorkspaceInput]) -> dict[str, Any]:
    """A workspace response that deliberately omits every infrastructure field.

    ``sandbox_job_ref`` and ``policy_snapshot`` are *not* here: the first is an
    opaque infra reference the client must never see, the second is internal. Only
    the status, lifecycle and the selected version ids are exposed.
    """
    return {
        "workspace_id": workspace.id,
        "run_id": workspace.run_id,
        "status": workspace.status,
        "created_at": workspace.created_at.isoformat(),
        "expires_at": workspace.expires_at.isoformat() if workspace.expires_at else None,
        "inputs": [
            {
                "material_version_id": item.material_version_id,
                "purpose": item.purpose,
                "read_only": item.read_only,
            }
            for item in inputs
        ],
    }


@router.post("/workspaces", status_code=201)
async def create_workspace(
    body: WorkspaceCreateRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Create a workspace selecting authorized, available material versions."""
    factory = _factory(request)
    async with factory() as session:
        selected: list[tuple[MaterialVersion, Material]] = []
        for version_id in body.material_version_ids:
            version = await session.get(MaterialVersion, version_id)
            if version is None or version.tenant_id != principal.tenant_id:
                # Unknown or another tenant's version: 404, never a leak that it
                # exists elsewhere.
                raise ApplicationError(
                    "RESOURCE_NOT_FOUND",
                    "a selected material version is not available",
                    status_code=404,
                )
            if version.status not in {"available", "superseded"}:
                raise ApplicationError(
                    "MATERIAL_NOT_AVAILABLE",
                    "only available immutable versions can be selected",
                    status_code=409,
                )
            material = await session.get(Material, version.material_id)
            assert material is not None
            selected.append((version, material))

        workspace = TaskWorkspace(
            tenant_id=principal.tenant_id,
            run_id=body.run_id,
            user_id=principal.user_id,
            session_id=body.session_id,
            status="ready",
        )
        session.add(workspace)
        await session.flush()
        inputs: list[WorkspaceInput] = []
        for version, material in selected:
            # A formal policy original is always read-only; a user upload may be
            # edited. The purpose follows from that, not from the request.
            read_only = material.source_type == "policy" or material.read_only
            item = WorkspaceInput(
                tenant_id=principal.tenant_id,
                workspace_id=workspace.id,
                material_version_id=version.id,
                purpose="read" if read_only else "edit",
                staged_hash=version.sha256,
                read_only=read_only,
            )
            session.add(item)
            inputs.append(item)
        await session.commit()
        return _workspace_view(workspace, inputs)


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str, principal: PrincipalDep, request: Request
) -> Any:
    async with _factory(request)() as session:
        workspace = await session.get(TaskWorkspace, workspace_id)
        if workspace is None or workspace.tenant_id != principal.tenant_id:
            raise ApplicationError("RESOURCE_NOT_FOUND", "no such workspace", status_code=404)
        inputs = list(
            (
                await session.execute(
                    select(WorkspaceInput).where(WorkspaceInput.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
        return _workspace_view(workspace, inputs)


@router.get("/workspaces/{workspace_id}/changes/{change_set_id}")
async def get_change_set(
    workspace_id: str, change_set_id: str, principal: PrincipalDep, request: Request
) -> Any:
    """Return a change set and its items for diff preview. No host paths or keys."""
    async with _factory(request)() as session:
        change_set = await session.get(ChangeSet, change_set_id)
        if (
            change_set is None
            or change_set.tenant_id != principal.tenant_id
            or change_set.workspace_id != workspace_id
        ):
            raise ApplicationError("RESOURCE_NOT_FOUND", "no such change set", status_code=404)
        items = list(
            (
                await session.execute(
                    select(ChangeSetItem).where(ChangeSetItem.change_set_id == change_set_id)
                )
            )
            .scalars()
            .all()
        )
        return {
            "change_set_id": change_set.id,
            "workspace_id": change_set.workspace_id,
            "state": change_set.state,
            "summary": change_set.summary,
            "side_effect_class": change_set.side_effect_class,
            "items": [
                {
                    # The normalized relative path is safe to show; it is contained
                    # by construction and names no host location.
                    "path": item.normalized_path,
                    "operation": item.operation,
                    "source_version_id": item.source_version_id,
                    "proposed_version_id": item.proposed_version_id,
                    "before_hash": item.before_hash,
                    "after_hash": item.after_hash,
                }
                for item in items
            ],
        }


@router.get("/workspaces/{workspace_id}/submissions/{submission_id}")
async def get_submission(
    workspace_id: str, submission_id: str, principal: PrincipalDep, request: Request
) -> Any:
    """Return a submission result: status and sanitized receipt, no raw payload."""
    async with _factory(request)() as session:
        job = await session.get(SubmissionJob, submission_id)
        if job is None or job.tenant_id != principal.tenant_id:
            raise ApplicationError("RESOURCE_NOT_FOUND", "no such submission", status_code=404)
        # Confirm it belongs to the run of a workspace this tenant owns.
        approval = await session.get(ApprovalRequest, job.approval_id)
        if approval is None or approval.tenant_id != principal.tenant_id:
            raise ApplicationError("RESOURCE_NOT_FOUND", "no such submission", status_code=404)
        return {
            "submission_id": job.id,
            "state": job.state,
            "connector_id": job.connector_id,
            "receipt_id": job.provider_receipt,
            # sanitized_result is already sanitized by the submission service; the
            # raw provider payload is never stored, so there is nothing to leak.
            "result": job.sanitized_result,
            "attempts": job.attempts,
        }


__all__ = ["router"]
