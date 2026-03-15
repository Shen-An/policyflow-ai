"""Tool registry and call-log API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from backend.app.api.deps import SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.tool import (
    ToolCallLogListResponse,
    ToolCallLogRead,
    ToolListResponse,
    ToolRunRequest,
    ToolRunResponse,
)
from backend.app.tools.registry import ToolRegistry

router = APIRouter(tags=["tools"])
SysAdminUser = Annotated[User, Depends(require_roles("sys_admin"))]
ToolAdminUser = Annotated[User, Depends(require_roles("kb_admin", "sys_admin"))]


@router.get("/api/tools", response_model=ToolListResponse)
def get_tools(request: Request, session: SessionDep, _: SysAdminUser) -> ToolListResponse:
    registry: ToolRegistry = request.app.state.tool_registry
    return registry.list(session)


@router.post("/api/tools/{name}/run", response_model=ToolRunResponse)
async def run_tool(
    name: str,
    data: ToolRunRequest,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> ToolRunResponse:
    registry: ToolRegistry = request.app.state.tool_registry
    return await registry.execute(
        session,
        name,
        user,
        data.input,
        data.conversation_id,
    )


@router.get("/api/tool-call-logs", response_model=ToolCallLogListResponse)
def get_tool_logs(
    request: Request,
    session: SessionDep,
    _: ToolAdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    tool_name: str | None = None,
    status: str | None = None,
) -> ToolCallLogListResponse:
    registry: ToolRegistry = request.app.state.tool_registry
    return registry.logs(session, page, page_size, tool_name, status)


@router.get("/api/tool-call-logs/{log_id}", response_model=ToolCallLogRead)
def get_tool_log(
    log_id: UUID,
    request: Request,
    session: SessionDep,
    _: ToolAdminUser,
) -> ToolCallLogRead:
    registry: ToolRegistry = request.app.state.tool_registry
    return registry.get_log(session, str(log_id))
