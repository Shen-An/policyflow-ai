"""Minimal MCP JSON-RPC client (stdio + streamable HTTP).

Implements enough of the MCP lifecycle for tools/list and tools/call so PolicyFlow
can honestly connect to real MCP servers without claiming enterprise SaaS connectors.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from backend.app.core.exceptions import ApplicationError


def _content_length_frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


async def _read_framed_message(stream: asyncio.StreamReader) -> dict[str, Any]:
    headers: dict[str, str] = {}
    while True:
        line = await stream.readline()
        if not line:
            raise ApplicationError("MCP_TRANSPORT_CLOSED", "MCP stdio stream closed", 502)
        if line in {b"\r\n", b"\n"}:
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        raise ApplicationError("MCP_PROTOCOL_ERROR", "Missing Content-Length from MCP server", 502)
    body = await stream.readexactly(length)
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ApplicationError("MCP_PROTOCOL_ERROR", "MCP message must be a JSON object", 502)
    return data


@dataclass
class MCPClient:
    """Stateful MCP session for one server connection."""

    transport: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 20.0
    _process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self.transport == "stdio":
            if not self.command:
                raise ApplicationError("MCP_CONFIG_INVALID", "stdio MCP requires command", 422)
            command_parts = shlex.split(self.command, posix=False)
            if self.args:
                command_parts.extend(self.args)
            process_env = None
            if self.env:
                import os

                process_env = {**os.environ, **self.env}
            self._process = await asyncio.create_subprocess_exec(
                *command_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=process_env,
            )
            await self._initialize_stdio()
            return
        if self.transport == "http":
            if not self.endpoint:
                raise ApplicationError("MCP_CONFIG_INVALID", "http MCP requires endpoint", 422)
            await self._initialize_http()
            return
        raise ApplicationError(
            "MCP_TRANSPORT_UNSUPPORTED",
            f"Unsupported MCP transport: {self.transport}",
            501,
        )

    async def close(self) -> None:
        process = self._process
        self._process = None
        self._initialized = False
        if process is None:
            return
        if process.stdin and not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _initialize_stdio(self) -> None:
        response = await self._stdio_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "policyflow-ai", "version": "0.1.0"},
            },
        )
        if "error" in response:
            raise ApplicationError(
                "MCP_INITIALIZE_FAILED",
                str(response["error"]),
                502,
            )
        await self._stdio_notify("notifications/initialized", {})
        self._initialized = True

    async def _initialize_http(self) -> None:
        response = await self._http_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "policyflow-ai", "version": "0.1.0"},
            },
        )
        if "error" in response:
            raise ApplicationError("MCP_INITIALIZE_FAILED", str(response["error"]), 502)
        await self._http_notify("notifications/initialized", {})
        self._initialized = True

    async def list_tools(self) -> list[str]:
        if self.transport == "stdio":
            response = await self._stdio_request("tools/list", {})
        else:
            response = await self._http_request("tools/list", {})
        if "error" in response:
            raise ApplicationError("MCP_TOOLS_LIST_FAILED", str(response["error"]), 502)
        result = response.get("result") or {}
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        names: list[str] = []
        for item in tools:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
        return sorted(names)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        params = {"name": name, "arguments": arguments or {}}
        if self.transport == "stdio":
            response = await self._stdio_request("tools/call", params)
        else:
            response = await self._http_request("tools/call", params)
        if "error" in response:
            raise ApplicationError("MCP_TOOL_CALL_FAILED", str(response["error"]), 502)
        result = response.get("result")
        if isinstance(result, dict):
            return result
        return {"result": result}

    async def _stdio_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise ApplicationError("MCP_TRANSPORT_CLOSED", "MCP stdio process is not running", 502)
        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._process.stdin.write(_content_length_frame(payload))
        await self._process.stdin.drain()
        while True:
            message = await asyncio.wait_for(
                _read_framed_message(self._process.stdout),
                timeout=self.timeout_seconds,
            )
            if message.get("id") == request_id:
                return message
            # Ignore unrelated notifications / other responses.

    async def _stdio_notify(self, method: str, params: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise ApplicationError("MCP_TRANSPORT_CLOSED", "MCP stdio process is not running", 502)
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self._process.stdin.write(_content_length_frame(payload))
        await self._process.stdin.drain()

    async def _http_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.endpoint is not None
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": method,
            "params": params,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.endpoint.rstrip("/"),
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ApplicationError("MCP_PROTOCOL_ERROR", "HTTP MCP response invalid", 502)
            return data

    async def _http_notify(self, method: str, params: dict[str, Any]) -> None:
        assert self.endpoint is not None
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await client.post(
                self.endpoint.rstrip("/"),
                json=payload,
                headers={"Content-Type": "application/json"},
            )
