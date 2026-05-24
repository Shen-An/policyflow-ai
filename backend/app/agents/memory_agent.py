"""Memory load/writeback agent for multi-layer conversational memory."""

from __future__ import annotations

from typing import Any, Protocol

from sqlmodel import Session

from backend.app.agents.base import MemoryWorkingSet
from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_logger
from backend.app.db.models import Conversation, MemoryItem, User
from backend.app.rag.protocols import LLMService
from backend.app.services.context_service import build_context
from backend.app.services.memory_extractor import extract_memory_events
from backend.app.services.memory_service import (
    MEMORY_TYPE_LONG_TERM,
    MEMORY_TYPE_PREFERENCE,
    find_similar_preference,
    list_fixed_memories,
    search_memories,
    touch_access,
    upsert_entity,
    write_memory,
)
from backend.app.services.memory_window import (
    compress_to_summary,
    load_recent_messages,
    messages_outside_window,
    parse_conversation_summary,
    should_compress,
    update_conversation_summary,
)
from backend.app.schemas.retrieval import Evidence

logger = get_logger(__name__)


class EmbeddingServiceProto(Protocol):
    @property
    def available(self) -> bool: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class MemoryAgent:
    """Loads a request-scoped working set and writes event-granularity memories."""

    def __init__(
        self,
        settings: Settings,
        llm_service: LLMService | None = None,
        embedding_service: EmbeddingServiceProto | None = None,
    ) -> None:
        self.settings = settings
        self.llm_service = llm_service
        self.embedding_service = embedding_service

    async def load(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        question: str,
    ) -> MemoryWorkingSet:
        fixed_items = list_fixed_memories(
            session,
            user.id,
            prefs_limit=self.settings.MEMORY_FIXED_PREFS_LIMIT,
            entity_limit=self.settings.MEMORY_ENTITY_LIMIT,
        )
        history = load_recent_messages(
            session,
            conversation.id,
            window_turns=self.settings.MEMORY_STM_WINDOW_TURNS,
        )
        # Exclude the just-persisted current user message from history prompt noise.
        if history and history[-1].get("role") == "user":
            last_content = str(history[-1].get("content") or "").strip()
            if last_content == question.strip():
                history = history[:-1]

        summary = parse_conversation_summary(conversation.summary)
        query_embedding = await self._safe_embed(question)
        owner_specs = [("user", user.id), ("conversation", conversation.id)]
        recalled_items = search_memories(
            session,
            owner_specs=owner_specs,
            query=question,
            query_embedding=query_embedding,
            top_k=self.settings.MEMORY_LTM_TOP_K,
        )
        # Avoid duplicating fixed prefs/entities in the recalled slot.
        fixed_ids = {item.id for item in fixed_items}
        recalled_items = [item for item in recalled_items if item.id not in fixed_ids]

        if fixed_items or recalled_items:
            touch_access(session, [*fixed_items[:3], *recalled_items])

        return MemoryWorkingSet(
            history=history,
            fixed_memories=[self._item_payload(item) for item in fixed_items],
            recalled_memories=[self._item_payload(item) for item in recalled_items],
            rolling_summary=str(summary.get("rolling_summary") or ""),
            memory_ids=[item.id for item in [*fixed_items, *recalled_items]],
        )

    async def writeback(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        question: str,
        answer: str,
        *,
        source_message_ids: list[str] | None = None,
    ) -> list[MemoryItem]:
        if not self.settings.MEMORY_WRITEBACK_ENABLED:
            return []

        fixed = list_fixed_memories(session, user.id, prefs_limit=20, entity_limit=20)
        existing_names = [
            str((item.meta_json or {}).get("entity_name") or item.content[:40])
            for item in fixed
            if item.memory_type == "entity"
        ]
        events = await extract_memory_events(
            question,
            answer,
            llm_service=self.llm_service,
            existing_entity_names=existing_names,
        )
        written: list[MemoryItem] = []
        for event in events:
            if event.event_type == "preference":
                if event.policy_related:
                    continue
                try:
                    existing = find_similar_preference(session, user.id, event.summary)
                    if existing is not None:
                        continue
                    embedding = await self._safe_embed(event.summary)
                    written.append(
                        write_memory(
                            session,
                            owner_type="user",
                            owner_id=user.id,
                            memory_type=MEMORY_TYPE_PREFERENCE,
                            content=event.summary,
                            source="summary",
                            confidence=max(0.6, event.salience),
                            embedding=embedding,
                            meta_json={
                                "event_type": event.event_type,
                                "salience": event.salience,
                                "source_message_ids": source_message_ids or [],
                            },
                        )
                    )
                except ApplicationError:
                    continue
                continue

            if event.event_type == "entity_update":
                for entity in event.entities or [{"name": event.summary, "type": "generic"}]:
                    name = str(entity.get("name") or "").strip()
                    if not name:
                        continue
                    embedding = await self._safe_embed(f"{name} {event.summary}")
                    written.append(
                        upsert_entity(
                            session,
                            user_id=user.id,
                            entity_type=str(entity.get("type") or "generic"),
                            name=name,
                            facts=event.facts or [event.summary],
                            content=event.summary,
                            source="summary",
                            confidence=max(0.6, event.salience),
                            embedding=embedding,
                            extra_meta={
                                "event_type": event.event_type,
                                "source_message_ids": source_message_ids or [],
                            },
                        )
                    )
                continue

            # decision / todo / conversation_fact → long-term when salient enough
            if event.salience < self.settings.MEMORY_LTM_SALIENCE_THRESHOLD:
                # Policy-only conversation facts stay out of LTM by default.
                if event.policy_related or event.event_type == "conversation_fact":
                    continue
            if event.policy_related and event.event_type == "conversation_fact":
                # Keep a light pointer only when salience is high (user-specific constraint).
                if event.salience < 0.75:
                    continue

            embedding = await self._safe_embed(event.summary)
            written.append(
                write_memory(
                    session,
                    owner_type="user",
                    owner_id=user.id,
                    memory_type=MEMORY_TYPE_LONG_TERM,
                    content=event.summary,
                    source="summary",
                    confidence=max(0.5, event.salience),
                    embedding=embedding,
                    meta_json={
                        "event_type": event.event_type,
                        "entities": event.entities,
                        "salience": event.salience,
                        "policy_related": event.policy_related,
                        "source_message_ids": source_message_ids or [],
                        "conversation_id": conversation.id,
                    },
                )
            )

        await self._maybe_compress_window(session, conversation)

        # Thin conversation-scoped trail for audit / backward compatibility.
        trail = write_memory(
            session,
            owner_type="conversation",
            owner_id=conversation.id,
            memory_type="conversation_summary",
            content=(
                f"用户问题：{question}\n助手回答摘要：{answer[:500]}"
            ),
            source="summary",
            confidence=0.55,
            meta_json={
                "event_type": "conversation_fact",
                "source_message_ids": source_message_ids or [],
                "extracted_event_count": len(events),
            },
        )
        written.append(trail)
        return written

    def build_answer_context(
        self,
        evidence: list[Evidence],
        working_set: MemoryWorkingSet,
    ) -> dict[str, Any]:
        # Reconstruct lightweight MemoryItem-like payloads are already dicts;
        # build_context expects MemoryItem for fixed/recalled only when objects.
        # For debugging/API we keep a pure dict context via manual assembly.
        return build_context(
            evidence,
            [],
            working_set.history,
            fixed_memories=[],
            recalled_memories=[],
            rolling_summary=working_set.rolling_summary,
        ) | {
            "fixed_memories": working_set.fixed_memories,
            "recalled_memories": working_set.recalled_memories,
            "non_authoritative_memory": [
                *working_set.fixed_memories,
                *working_set.recalled_memories,
            ],
        }

    async def _maybe_compress_window(
        self,
        session: Session,
        conversation: Conversation,
    ) -> None:
        older = messages_outside_window(
            session,
            conversation.id,
            window_turns=self.settings.MEMORY_STM_WINDOW_TURNS,
        )
        if not older:
            return
        # Trigger only when total history exceeds compress threshold.
        total_like = len(older) + self.settings.MEMORY_STM_WINDOW_TURNS * 2
        if not should_compress(
            total_like,
            threshold_turns=self.settings.MEMORY_COMPRESS_TURN_THRESHOLD,
        ):
            return

        prev = parse_conversation_summary(conversation.summary)
        already = set(prev.get("compressed_message_ids") or [])
        to_compress = [item for item in older if item.get("id") not in already]
        if not to_compress:
            return

        new_summary = await compress_to_summary(
            to_compress,
            prev,
            llm_service=self.llm_service,
        )
        # Unload salient compressed content into LTM as a single rolling event.
        rolling = str(new_summary.get("rolling_summary") or "").strip()
        if rolling and rolling != str(prev.get("rolling_summary") or "").strip():
            embedding = await self._safe_embed(rolling)
            write_memory(
                session,
                owner_type="conversation",
                owner_id=conversation.id,
                memory_type=MEMORY_TYPE_LONG_TERM,
                content=f"会话压缩摘要：{rolling[:500]}",
                source="summary",
                confidence=0.55,
                embedding=embedding,
                meta_json={
                    "event_type": "conversation_fact",
                    "salience": 0.55,
                    "unloaded_from_stm": True,
                    "compressed_message_ids": new_summary.get("compressed_message_ids") or [],
                },
            )
        update_conversation_summary(session, conversation, new_summary)

    async def _safe_embed(self, text: str) -> list[float] | None:
        service = self.embedding_service
        cleaned = (text or "").strip()
        if not cleaned or service is None:
            return None
        try:
            if not getattr(service, "available", True):
                return None
            vectors = await service.embed([cleaned[:2000]])
            if vectors and vectors[0]:
                return list(vectors[0])
        except Exception as exc:  # embedding is best-effort for memory
            logger.warning("Memory embedding failed: %s", type(exc).__name__)
        return None

    @staticmethod
    def _item_payload(item: MemoryItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "type": item.memory_type,
            "content": item.content,
            "confidence": item.confidence,
            "source": item.source,
            "meta": dict(item.meta_json or {}),
        }

    # Backward-compatible sync entry used by older tests/call sites.
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
            meta_json={"event_type": "conversation_fact", "legacy": True},
        )
