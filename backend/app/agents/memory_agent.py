"""Conversation-summary memory updater."""

from sqlmodel import Session

from backend.app.db.models import MemoryItem
from backend.app.services.memory_service import write_memory


class MemoryAgent:
    def run(
        self,
        session: Session,
        conversation_id: str,
        question: str,
        answer: str,
    ) -> MemoryItem:
        content = f"用户问题：{question}{chr(10)}助手回答摘要：{answer[:500]}"
        return write_memory(
            session,
            owner_type="conversation",
            owner_id=conversation_id,
            memory_type="conversation_summary",
            content=content,
            source="summary",
            confidence=0.6,
        )
