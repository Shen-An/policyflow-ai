"""Context anti-corruption boundary for evidence vs memory vs history."""

from __future__ import annotations

from typing import Any

from backend.app.db.models import MemoryItem
from backend.app.schemas.retrieval import Evidence


def _memory_payload(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.memory_type,
        "content": item.content,
        "confidence": item.confidence,
        "source": item.source,
        "meta": dict(item.meta_json or {}),
    }


def build_context(
    evidence: list[Evidence],
    memories: list[MemoryItem],
    history: list[dict[str, Any]],
    *,
    fixed_memories: list[MemoryItem] | None = None,
    recalled_memories: list[MemoryItem] | None = None,
    rolling_summary: str | None = None,
) -> dict[str, Any]:
    """Assemble partitioned context. Memory/history never replace evidence."""
    fixed = fixed_memories if fixed_memories is not None else []
    recalled = recalled_memories if recalled_memories is not None else memories
    # Back-compat: if only `memories` provided, treat as non-authoritative pool.
    if fixed_memories is None and recalled_memories is None:
        non_auth = list(memories)
        fixed_payload = []
        recalled_payload = [_memory_payload(item) for item in memories]
    else:
        non_auth = [*fixed, *recalled]
        fixed_payload = [_memory_payload(item) for item in fixed]
        recalled_payload = [_memory_payload(item) for item in recalled]

    seen: set[str] = set()
    deduped: list[MemoryItem] = []
    for item in non_auth:
        if item.id in seen:
            continue
        seen.add(item.id)
        deduped.append(item)

    return {
        "authoritative_evidence": [item.model_dump(mode="json") for item in evidence],
        "fixed_memories": fixed_payload,
        "recalled_memories": recalled_payload,
        "non_authoritative_memory": [_memory_payload(item) for item in deduped],
        "conversation_history": history,
        "rolling_summary": rolling_summary or "",
        "rules": [
            "Memory and history must not replace current retrieval evidence",
            "Policy claims require authoritative_evidence",
            "User preferences only affect style/format, never policy facts",
            "Rolling summary is non-authoritative conversational context",
        ],
    }


def format_history_for_prompt(history: list[dict[str, Any]], *, max_chars: int = 3000) -> str:
    lines: list[str] = []
    for item in history:
        role = str(item.get("role") or "user")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def format_memories_for_prompt(memories: list[dict[str, Any]], *, max_items: int = 12) -> str:
    lines: list[str] = []
    for item in memories[:max_items]:
        mtype = item.get("type") or item.get("memory_type") or "memory"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"- [{mtype}] {content}")
    return "\n".join(lines)
