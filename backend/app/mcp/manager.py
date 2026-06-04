"""MCP manager: mock adapters + real stdio/http clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.mcp_security import reveal_command, reveal_config
from backend.app.db.models import MCPServer
from backend.app.mcp.client import MCPClient
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

    def _config(self, server: MCPServer) -> dict[str, Any]:
        raw = reveal_config(server.config, self.settings.SECRET_KEY)
        return raw if isinstance(raw, dict) else {}

    def _client_for(self, server: MCPServer) -> MCPClient:
        config = self._config(server)
        timeout = float(config.get("timeout_seconds") or 20.0)
        args = config.get("args") if isinstance(config.get("args"), list) else []
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        command = reveal_command(server.command, self.settings.SECRET_KEY) or None
        return MCPClient(
            transport=server.integration_mode,
            command=command,
            args=[str(item) for item in args],
            endpoint=server.endpoint,
            env={str(key): str(value) for key, value in env.items()},
            timeout_seconds=timeout,
        )

    def health(self, server: MCPServer) -> MCPHealthResult:
        if server.integration_mode == "mock":
            return MCPHealthResult("healthy", sorted(self.mock_server.tools))
        try:
            return self._health_sync_bridge(server)
        except ApplicationError as exc:
            return MCPHealthResult(
                "unhealthy",
                [],
                exc.code,
                exc.message,
            )
        except Exception as exc:  # noqa: BLE001 - surface external process failures
            return MCPHealthResult(
                "unhealthy",
                [],
                "MCP_HEALTH_FAILED",
                f"{type(exc).__name__}: {exc}",
            )

    def _health_sync_bridge(self, server: MCPServer) -> MCPHealthResult:
        import asyncio

        async def _run() -> MCPHealthResult:
            async with self._client_for(server) as client:
                tools = await client.list_tools()
            return MCPHealthResult("healthy", tools)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        # health() is sync (route is sync); if already in loop, run in a new loop threadlessly.
        # Prefer creating a task-less dedicated loop via asyncio.run in a worker when nested.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_run())).result(timeout=30)

    async def health_async(self, server: MCPServer) -> MCPHealthResult:
        if server.integration_mode == "mock":
            return MCPHealthResult("healthy", sorted(self.mock_server.tools))
        try:
            async with self._client_for(server) as client:
                tools = await client.list_tools()
            return MCPHealthResult("healthy", tools)
        except ApplicationError as exc:
            return MCPHealthResult("unhealthy", [], exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            return MCPHealthResult(
                "unhealthy",
                [],
                "MCP_HEALTH_FAILED",
                f"{type(exc).__name__}: {exc}",
            )

    async def call(
        self,
        server: MCPServer,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not server.enabled:
            raise ApplicationError("MCP_SERVER_DISABLED", "MCP server is disabled", 409)
        config = self._config(server)

        if server.integration_mode == "mock":
            # Honest mock: require mock mode marker when present.
            if config and config.get("mode") not in {None, "mock"}:
                raise ApplicationError(
                    "MCP_CONFIG_INVALID",
                    "mock integration_mode expects config.mode=mock",
                    422,
                )
            result = await self.mock_server.call(tool_name, arguments)
            if isinstance(result, dict) and "status" not in result:
                result = {**result, "status": "mock"}
            return result

        if server.integration_mode not in {"stdio", "http"}:
            raise ApplicationError(
                "MCP_TRANSPORT_UNSUPPORTED",
                f"Unsupported integration_mode: {server.integration_mode}",
                501,
            )
        async with self._client_for(server) as client:
            result = await client.call_tool(tool_name, arguments or {})
        # Label real protocol responses clearly.
        if isinstance(result, dict):
            return {
                **result,
                "status": result.get("status") or "ok",
                "integration_mode": server.integration_mode,
            }
        return {
            "status": "ok",
            "integration_mode": server.integration_mode,
            "result": result,
        }
