"""MCP server configuration and health services."""

from datetime import UTC

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError, ConflictError
from backend.app.core.mcp_security import (
    config_summary,
    protect_command,
    protect_config,
    reveal_command,
    reveal_config,
)
from backend.app.db.models import MCPServer, User, utc_now
from backend.app.mcp.manager import MCPManager
from backend.app.schemas.mcp import (
    MCPHealthResponse,
    MCPServerCreate,
    MCPServerListResponse,
    MCPServerRead,
    MCPServerUpdate,
)
from backend.app.services.audit_service import record_audit


def _to_read(server: MCPServer) -> MCPServerRead:
    last_checked_at = server.last_checked_at
    if last_checked_at is not None and last_checked_at.tzinfo is None:
        last_checked_at = last_checked_at.replace(tzinfo=UTC)
    return MCPServerRead(
        id=server.id,
        name=server.name,
        type=server.server_type,
        integration_mode=server.integration_mode,
        endpoint=server.endpoint,
        command_configured=bool(server.command),
        config_summary=config_summary(server.config),
        enabled=server.enabled,
        health_status=server.health_status,
        tools=server.tools,
        last_error_code=server.last_error_code,
        last_error_message=server.last_error_message,
        last_checked_at=last_checked_at,
    )


def create_mcp_server(
    session: Session,
    settings: Settings,
    user: User,
    data: MCPServerCreate,
    ip_address: str | None = None,
) -> MCPServerRead:
    server = MCPServer(
        name=data.name,
        server_type=data.type,
        integration_mode=data.integration_mode,
        endpoint=data.endpoint,
        command=protect_command(data.command, settings.SECRET_KEY),
        config=protect_config(data.config, settings.SECRET_KEY),
        enabled=data.enabled,
    )
    session.add(server)
    try:
        session.flush()
        record_audit(
            session,
            action="mcp.server.create",
            target_type="mcp_server",
            actor_id=user.id,
            target_id=server.id,
            detail={
                "name": server.name,
                "type": server.server_type,
                "integration_mode": server.integration_mode,
                "enabled": server.enabled,
            },
            ip_address=ip_address,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("MCP_SERVER_EXISTS", "MCP server name already exists") from exc
    session.refresh(server)
    return _to_read(server)


def list_mcp_servers(session: Session) -> MCPServerListResponse:
    servers = session.exec(select(MCPServer).order_by(MCPServer.name)).all()
    return MCPServerListResponse(items=[_to_read(server) for server in servers])


def update_mcp_server(
    session: Session,
    settings: Settings,
    user: User,
    server_id: str,
    data: MCPServerUpdate,
    ip_address: str | None = None,
) -> MCPServerRead:
    server = session.get(MCPServer, server_id)
    if server is None:
        raise ApplicationError("MCP_SERVER_NOT_FOUND", "MCP server not found", 404)
    current = {
        "name": server.name,
        "type": server.server_type,
        "integration_mode": server.integration_mode,
        "endpoint": server.endpoint,
        "command": reveal_command(server.command, settings.SECRET_KEY) or None,
        "config": reveal_config(server.config, settings.SECRET_KEY),
        "enabled": server.enabled,
    }
    changes = data.model_dump(exclude_unset=True)
    validated = MCPServerCreate.model_validate({**current, **changes})
    server.name = validated.name
    server.server_type = validated.type
    server.integration_mode = validated.integration_mode
    server.endpoint = validated.endpoint
    server.command = protect_command(validated.command, settings.SECRET_KEY)
    server.config = protect_config(validated.config, settings.SECRET_KEY)
    server.enabled = validated.enabled
    server.health_status = "unknown"
    server.tools = []
    server.last_error_code = None
    server.last_error_message = None
    server.last_checked_at = None
    server.updated_at = utc_now()
    session.add(server)
    try:
        record_audit(
            session,
            action="mcp.server.update",
            target_type="mcp_server",
            actor_id=user.id,
            target_id=server.id,
            detail={"name": server.name, "changed_fields": sorted(changes)},
            ip_address=ip_address,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("MCP_SERVER_EXISTS", "MCP server name already exists") from exc
    session.refresh(server)
    return _to_read(server)


def check_mcp_health(
    session: Session,
    manager: MCPManager,
    user: User,
    server_id: str,
    ip_address: str | None = None,
) -> MCPHealthResponse:
    server = session.get(MCPServer, server_id)
    if server is None:
        raise ApplicationError("MCP_SERVER_NOT_FOUND", "MCP server not found", 404)
    result = manager.health(server)
    checked_at = utc_now()
    server.health_status = result.status
    server.tools = result.tools
    server.last_error_code = result.error_code
    server.last_error_message = result.error_message
    server.last_checked_at = checked_at
    server.updated_at = utc_now()
    session.add(server)
    record_audit(
        session,
        action="mcp.server.health_check",
        target_type="mcp_server",
        actor_id=user.id,
        target_id=server.id,
        detail={
            "health_status": result.status,
            "error_code": result.error_code,
            "tool_count": len(result.tools),
        },
        ip_address=ip_address,
    )
    session.commit()
    return MCPHealthResponse(
        server_id=server.id,
        health_status=result.status,
        tools=result.tools,
        checked_at=checked_at,
        error_code=result.error_code,
        error_message=result.error_message,
    )
