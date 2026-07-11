"""Safe in-process MCP mock tools."""

from typing import Any


async def email_create_draft(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"status": "mock", "draft_id": "mock-email-draft", "input": arguments}


async def calendar_create_event(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"status": "mock", "event_id": "mock-calendar-event", "input": arguments}


async def feishu_send_message(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"status": "mock", "message_id": "mock-feishu-message", "input": arguments}


async def confluence_search(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"status": "mock", "results": [], "input": arguments}
