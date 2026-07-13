"""Short-term memory window helpers: recent turns and rolling summary."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, col, select

from backend.app.db.models import Conversation, Message, utc_now
from backend.app.rag.protocols import LLMService


def parse_conversation_summary(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {
            "rolling_summary": "",
            "active_todos": [],
            "key_entities": [],
            "compressed_message_ids": [],
            "updated_at": None,
        }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "rolling_summary": raw.strip(),
            "active_todos": [],
            "key_entities": [],
            "compressed_message_ids": [],
            "updated_at": None,
        }
    if not isinstance(data, dict):
        return {
            "rolling_summary": str(data),
            "active_todos": [],
            "key_entities": [],
            "compressed_message_ids": [],
            "updated_at": None,
        }
    return {
        "rolling_summary": str(data.get("rolling_summary") or ""),
        "active_todos": list(data.get("active_todos") or []),
        "key_entities": list(data.get("key_entities") or []),
        "compressed_message_ids": list(data.get("compressed_message_ids") or []),
        "updated_at": data.get("updated_at"),
    }


def dump_conversation_summary(summary: dict[str, Any]) -> str:
    payload = {
        "rolling_summary": str(summary.get("rolling_summary") or ""),
        "active_todos": list(summary.get("active_todos") or [])[:20],
        "key_entities": list(summary.get("key_entities") or [])[:20],
        "compressed_message_ids": list(summary.get("compressed_message_ids") or [])[-200:],
        "updated_at": summary.get("updated_at") or utc_now().isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False)


def load_recent_messages(
    session: Session,
    conversation_id: str,
    *,
    window_turns: int = 6,
) -> list[dict[str, Any]]:
    """Return the last K turns (approx 2 messages/turn) as prompt history dicts."""
    limit = max(window_turns, 0) * 2
    if limit == 0:
        return []
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(col(Message.created_at).desc())
        .limit(limit)
    ).all()
    ordered = list(reversed(messages))
    return [
        {
            "role": message.role,
            "content": message.content,
            "id": message.id,
            "created_at": message.created_at.isoformat()
            if getattr(message.created_at, "isoformat", None)
            else str(message.created_at),
        }
        for message in ordered
    ]


def count_messages(session: Session, conversation_id: str) -> int:
    messages = session.exec(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all()
    return len(messages)


def should_compress(message_count: int, *, threshold_turns: int = 8) -> bool:
    return message_count > max(threshold_turns, 1) * 2


async def compress_to_summary(
    old_messages: list[dict[str, Any]],
    prev_summary: dict[str, Any],
    *,
    llm_service: LLMService | None = None,
) -> dict[str, Any]:
    """Compress older messages into an updated rolling summary structure."""
    previous = str(prev_summary.get("rolling_summary") or "").strip()
    transcript = "\n".join(
        f"{item.get('role')}: {item.get('content')}" for item in old_messages if item.get("content")
    )
    if not transcript and not previous:
        return prev_summary

    new_text = ""
    if llm_service is not None and getattr(llm_service, "available", True) and transcript:
        system_prompt = (
            "你是会话摘要器。把历史对话压缩成简洁中文滚动摘要，保留："
            "用户目标、已确认决策、待办、偏好、关键实体。"
            "不要写入制度条款正文。只输出摘要正文。"
        )
        user_prompt = f"已有摘要：{previous or '无'}\n新增对话：\n{transcript[:4000]}"
        try:
            new_text = (await llm_service.complete(system_prompt, user_prompt)).strip()
        except Exception:
            new_text = ""
    if not new_text:
        snippet = "；".join(
            f"{item.get('role')}:{str(item.get('content') or '')[:80]}"
            for item in old_messages[-6:]
        )
        new_text = "；".join(part for part in (previous, snippet) if part)[:1200]

    compressed_ids = list(prev_summary.get("compressed_message_ids") or [])
    for item in old_messages:
        message_id = item.get("id")
        if message_id and message_id not in compressed_ids:
            compressed_ids.append(message_id)

    return {
        "rolling_summary": new_text[:2000],
        "active_todos": list(prev_summary.get("active_todos") or []),
        "key_entities": list(prev_summary.get("key_entities") or []),
        "compressed_message_ids": compressed_ids[-200:],
        "updated_at": utc_now().isoformat(),
    }


def update_conversation_summary(
    session: Session,
    conversation: Conversation,
    summary: dict[str, Any],
) -> Conversation:
    conversation.summary = dump_conversation_summary(summary)
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def messages_outside_window(
    session: Session,
    conversation_id: str,
    *,
    window_turns: int,
) -> list[dict[str, Any]]:
    """Messages older than the STM window, for compression/unload."""
    all_messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(col(Message.created_at).asc())
    ).all()
    keep = max(window_turns, 0) * 2
    if keep <= 0 or len(all_messages) <= keep:
        return []
    older = all_messages[: len(all_messages) - keep]
    return [
        {
            "role": message.role,
            "content": message.content,
            "id": message.id,
        }
        for message in older
    ]
