"""In-process MCP mock server registry."""

from typing import Any

from backend.app.core.exceptions import ApplicationError
from backend.app.mcp.mock_tools import (
    calendar_create_event,
    confluence_search,
    email_create_draft,
    feishu_send_message,
)


class MockMCPServer:
    def __init__(self) -> None:
        self.tools = {
            "mcp.email.create_draft": email_create_draft,
            "mcp.calendar.create_event": calendar_create_event,
            "mcp.feishu.send_message_mock": feishu_send_message,
            "mcp.confluence.search_mock": confluence_search,
        }

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise ApplicationError("MCP_TOOL_NOT_FOUND", "MCP mock tool not found", 404)
        return await tool(arguments)
