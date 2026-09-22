"""Built-in Tool handlers."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlmodel import Session

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import MCPServer, User
from backend.app.db.repositories import UnitOfWork, require_tenant
from backend.app.mcp.manager import MCPManager
from backend.app.schemas.draft import DraftCreate, DraftUpdate
from backend.app.services.draft_service import create_draft, update_draft
from backend.app.services.memory_service import read_memory, write_memory
from backend.app.tools.base import ToolHandler

UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]


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


def _resolve_memory_owner(user: User, payload: dict[str, Any]) -> tuple[str, str]:
    """Memory tools may only access the current user's memory namespace."""
    owner_type = str(payload.get("owner_type") or "user")
    owner_id = str(payload.get("owner_id") or user.id)
    if owner_type != "user":
        raise ApplicationError(
            "PERMISSION_DENIED",
            "Memory tools only support owner_type=user for the current user",
            403,
        )
    if owner_id != user.id:
        raise ApplicationError(
            "PERMISSION_DENIED",
            "Cannot access another user's memory",
            403,
        )
    return owner_type, owner_id


def memory_write_tool(uow_factory: UnitOfWorkFactory) -> ToolHandler:
    """Build a memory-write handler bound to the async unit-of-work factory.

    Memory is tenant-owned data, so the write runs on the tenant-qualified
    asynchronous repository with the tenant taken from the account, never from the
    tool payload. The synchronous ``session`` is accepted to satisfy the tool
    protocol but is not used for the memory write.
    """

    async def handler(
        session: Session,
        user: User,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner_type, owner_id = _resolve_memory_owner(user, payload)
        tenant_id = require_tenant(user.tenant_id, "memory.write")
        async with uow_factory() as uow:
            item = await write_memory(
                uow.memories,
                tenant_id,
                owner_type=owner_type,
                owner_id=owner_id,
                memory_type=str(payload.get("memory_type") or "user_preference"),
                content=str(payload.get("content") or ""),
                source="tool",
                confidence=float(payload.get("confidence") or 0.5),
            )
            result = item.model_dump(mode="json")
            await uow.commit()
        return result

    return handler


def memory_read_tool(uow_factory: UnitOfWorkFactory) -> ToolHandler:
    """Build a memory-read handler bound to the async unit-of-work factory."""

    async def handler(
        session: Session,
        user: User,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner_type, owner_id = _resolve_memory_owner(user, payload)
        tenant_id = require_tenant(user.tenant_id, "memory.read")
        async with uow_factory() as uow:
            items = await read_memory(uow.memories, tenant_id, owner_type, owner_id)
            return {"items": [item.model_dump(mode="json") for item in items]}

    return handler


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
