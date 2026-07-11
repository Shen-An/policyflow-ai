"""MCP mock manager and health surface."""

from dataclasses import dataclass
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.mcp_security import reveal_config
from backend.app.db.models import MCPServer
from backend.app.mcp.mock_server import MockMCPServer


@dataclass(frozen=True)
class MCPHealthResult:
    status: str
    tools: list[str]
    error_code: str | None = None
    error_message: str | None = None


class MCPManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mock_server = MockMCPServer()

    def health(self, server: MCPServer) -> MCPHealthResult:
        if server.integration_mode == "mock":
            return MCPHealthResult("healthy", sorted(self.mock_server.tools))
        return MCPHealthResult(
            "unhealthy",
            [],
            "MCP_EXTERNAL_NOT_CONFIGURED",
            "External MCP health checks are not configured in the MVP",
        )

    async def call(
        self,
        server: MCPServer,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not server.enabled:
            raise ApplicationError("MCP_SERVER_DISABLED", "MCP server is disabled", 409)
        config = reveal_config(server.config, self.settings.SECRET_KEY)
        if server.integration_mode != "mock" or config.get("mode") != "mock":
            raise ApplicationError("MCP_SERVER_UNAVAILABLE", "Only mock MCP is available", 503)
        return await self.mock_server.call(tool_name, arguments)
