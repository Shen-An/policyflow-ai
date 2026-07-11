"""Built-in Tool handlers."""

from typing import Any

from sqlmodel import Session

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import MCPServer, User
from backend.app.mcp.manager import MCPManager
from backend.app.schemas.draft import DraftCreate, DraftUpdate
from backend.app.services.draft_service import create_draft, update_draft
from backend.app.services.memory_service import read_memory, write_memory
from backend.app.tools.base import ToolHandler


async def draft_create_tool(
    session: Session,
    user: User,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return create_draft(session, user, DraftCreate.model_validate(payload)).model_dump(mode="json")


async def draft_update_tool(
    session: Session,
    user: User,
    payload: dict[str, Any],
) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "")
    if not draft_id:
        raise ApplicationError("VALIDATION_ERROR", "draft_id is required", 422)
    data = DraftUpdate.model_validate(payload.get("changes") or {})
    return update_draft(session, user, draft_id, data).model_dump(mode="json")


async def memory_write_tool(
    session: Session,
    user: User,
    payload: dict[str, Any],
) -> dict[str, Any]:
    item = write_memory(
        session,
        owner_type=str(payload.get("owner_type") or "user"),
        owner_id=str(payload.get("owner_id") or user.id),
        memory_type=str(payload.get("memory_type") or "user_preference"),
        content=str(payload.get("content") or ""),
        source="tool",
        confidence=float(payload.get("confidence") or 0.5),
    )
    return item.model_dump(mode="json")


async def memory_read_tool(
    session: Session,
    user: User,
    payload: dict[str, Any],
) -> dict[str, Any]:
    items = read_memory(
        session,
        str(payload.get("owner_type") or "user"),
        str(payload.get("owner_id") or user.id),
    )
    return {"items": [item.model_dump(mode="json") for item in items]}


def mcp_call_tool(manager: MCPManager) -> ToolHandler:
    async def handler(
        session: Session,
        user: User,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        server_id = str(payload.get("server_id") or "")
        server = session.get(MCPServer, server_id)
        if server is None:
            raise ApplicationError("MCP_SERVER_NOT_FOUND", "MCP server not found", 404)
        return await manager.call(
            server,
            str(payload.get("tool_name") or ""),
            dict(payload.get("arguments") or {}),
        )

    return handler
