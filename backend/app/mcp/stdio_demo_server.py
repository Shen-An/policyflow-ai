"""Minimal stdio MCP demo server for local/real-protocol tests.

Run:
  python -m backend.app.mcp.stdio_demo_server

Speaks Content-Length framed JSON-RPC and exposes:
  - echo
  - time_now
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    data = json.loads(body.decode("utf-8"))
    return data if isinstance(data, dict) else None


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


TOOLS = [
    {
        "name": "echo",
        "description": "Echo arguments back. Real MCP demo tool.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
    },
    {
        "name": "time_now",
        "description": "Return current UTC timestamp.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "policyflow-stdio-demo", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name == "echo":
            text = str(arguments.get("text") or "")
            result = {
                "content": [{"type": "text", "text": text}],
                "structuredContent": {"echo": text, "status": "ok", "mode": "stdio-real"},
                "isError": False,
            }
        elif name == "time_now":
            now = datetime.now(UTC).isoformat()
            result = {
                "content": [{"type": "text", "text": now}],
                "structuredContent": {"utc": now, "status": "ok", "mode": "stdio-real"},
                "isError": False,
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    while True:
        message = _read_message()
        if message is None:
            break
        response = _handle_request(message)
        if response is not None:
            _write_message(response)


if __name__ == "__main__":
    main()
