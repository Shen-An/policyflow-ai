"""MCP mock configuration API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from backend.app.api.deps import SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.mcp import (
    MCPHealthResponse,
    MCPServerCreate,
    MCPServerListResponse,
    MCPServerRead,
    MCPServerUpdate,
)
from backend.app.services.mcp_service import (
    check_mcp_health,
    create_mcp_server,
    list_mcp_servers,
    update_mcp_server,
)

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])
SysAdminUser = Annotated[User, Depends(require_roles("sys_admin"))]


@router.get("", response_model=MCPServerListResponse)
def get_mcp_servers(session: SessionDep, _: SysAdminUser) -> MCPServerListResponse:
    return list_mcp_servers(session)


@router.post("", response_model=MCPServerRead, status_code=status.HTTP_201_CREATED)
def post_mcp_server(
    data: MCPServerCreate,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> MCPServerRead:
    return create_mcp_server(
        session,
        request.app.state.settings,
        user,
        data,
        request.client.host if request.client else None,
    )


@router.put("/{server_id}", response_model=MCPServerRead)
@router.patch("/{server_id}", response_model=MCPServerRead)
def put_mcp_server(
    server_id: UUID,
    data: MCPServerUpdate,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> MCPServerRead:
    return update_mcp_server(
        session,
        request.app.state.settings,
        user,
        str(server_id),
        data,
        request.client.host if request.client else None,
    )


@router.post("/{server_id}/health-check", response_model=MCPHealthResponse)
def post_mcp_health(
    server_id: UUID,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> MCPHealthResponse:
    return check_mcp_health(
        session,
        request.app.state.mcp_manager,
        user,
        str(server_id),
        request.client.host if request.client else None,
    )
