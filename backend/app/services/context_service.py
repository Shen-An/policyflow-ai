"""Context anti-corruption boundary."""

from typing import Any

from backend.app.db.models import MemoryItem
from backend.app.schemas.retrieval import Evidence


def build_context(
    evidence: list[Evidence],
    memories: list[MemoryItem],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "authoritative_evidence": [item.model_dump(mode="json") for item in evidence],
        "non_authoritative_memory": [
            {"type": item.memory_type, "content": item.content, "confidence": item.confidence}
            for item in memories
        ],
        "conversation_history": history,
        "rules": [
            "Memory and history must not replace current retrieval evidence",
            "Policy claims require authoritative_evidence",
        ],
    }
