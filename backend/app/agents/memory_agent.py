"""Memory load/writeback agent for multi-layer conversational memory."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from backend.app.agents.base import MemoryWorkingSet
from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_logger
from backend.app.db.models import Conversation, MemoryItem, User
from backend.app.db.repositories import UnitOfWork, require_tenant
from backend.app.rag.protocols import LLMService
from backend.app.schemas.retrieval import Evidence
from backend.app.services.context_service import build_context
from backend.app.services.memory_extractor import extract_memory_events
from backend.app.services.memory_service import (
    MEMORY_TYPE_LONG_TERM,
    MEMORY_TYPE_PREFERENCE,
    find_similar_preference,
    list_fixed_memories,
    search_memories_scored,
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

logger = get_logger(__name__)

# A factory returns a unit of work bound to the application's async engine. Memory
# is non-authoritative side data on its own table, so it runs on a short async
# transaction of its own rather than sharing the chat request's synchronous one.
UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]


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
        uow_factory: UnitOfWorkFactory | None = None,
    ) -> None:
        self.settings = settings
        self.llm_service = llm_service
        self.embedding_service = embedding_service
        self._uow_factory = uow_factory

    def _unit_of_work(self) -> AbstractAsyncContextManager[UnitOfWork]:
        """Open a unit of work for this agent's memory reads and writes.

        When the application injected a factory (the common case) the unit of work
        rides the app's async engine. Absent one - a bare ``MemoryAgent(settings)``
        in a test or the synchronous fallback - it falls back to the process-wide
        async session factory, which resolves the same configured database.
        """
        if self._uow_factory is not None:
            return self._uow_factory()
        return UnitOfWork()

    async def load(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        question: str,
    ) -> MemoryWorkingSet:
        # History and the rolling summary live on the messages / conversations
        # tables and stay on the request's synchronous session.
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
        tenant_id = require_tenant(user.tenant_id, "MemoryAgent.load")

        # Memory items are tenant-owned; the reads and the access-heat write share
        # one tenant-qualified unit of work. Payloads are assembled inside the
        # block so they do not depend on the session staying open afterwards.
        async with self._unit_of_work() as uow:
            repo = uow.memories
            fixed_items = await list_fixed_memories(
                repo,
                tenant_id,
                user.id,
                prefs_limit=self.settings.MEMORY_FIXED_PREFS_LIMIT,
                entity_limit=self.settings.MEMORY_ENTITY_LIMIT,
            )
            scored_hits = await search_memories_scored(
                repo,
                tenant_id,
                owner_specs=owner_specs,
                query=question,
                query_embedding=query_embedding,
                top_k=self.settings.MEMORY_LTM_TOP_K,
                decay_lambda=self.settings.MEMORY_RANK_DECAY_LAMBDA,
                access_boost_cap=self.settings.MEMORY_RANK_ACCESS_BOOST_CAP,
            )
            # Avoid duplicating fixed prefs/entities in the recalled slot.
            fixed_ids = {item.id for item in fixed_items}
            scored_hits = [
                (score, item) for score, item in scored_hits if item.id not in fixed_ids
            ]
            recalled_items = [item for _, item in scored_hits]

            # Build the working set from the reads first, so the result never
            # depends on the access-heat write below succeeding.
            working_set = MemoryWorkingSet(
                history=history,
                fixed_memories=[self._item_payload(item) for item in fixed_items],
                recalled_memories=[
                    self._item_payload(item, rank_score=score) for score, item in scored_hits
                ],
                rolling_summary=str(summary.get("rolling_summary") or ""),
                memory_ids=[item.id for item in [*fixed_items, *recalled_items]],
            )

            # Access heat is non-authoritative ranking bookkeeping. Recording it must
            # never fail a turn, so a transient store lock - which a single-writer
            # SQLite in development can raise while the request's synchronous
            # transaction is still open - is downgraded to a skipped update rather
            # than a failed load. PostgreSQL does not take that lock.
            if fixed_items or recalled_items:
                try:
                    await touch_access(repo, tenant_id, [*fixed_items[:3], *recalled_items])
                    await uow.commit()
                except OperationalError:
                    logger.warning("Memory access-heat update skipped: store busy")
                    await uow.rollback()
        return working_set

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

        tenant_id = require_tenant(user.tenant_id, "MemoryAgent.writeback")
        # Every memory write this turn shares one tenant-qualified unit of work, so
        # either they all land or none do, and each row carries the tenant the
        # enforced schema requires.
        async with self._unit_of_work() as uow:
            repo = uow.memories
            fixed = await list_fixed_memories(
                repo,
                tenant_id,
                user.id,
                prefs_limit=20,
                entity_limit=20,
            )
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
                        existing = await find_similar_preference(
                            repo, tenant_id, user.id, event.summary
                        )
                        if existing is not None:
                            continue
                        embedding = await self._safe_embed(event.summary)
                        written.append(
                            await write_memory(
                                repo,
                                tenant_id,
                                owner_type="user",
                                owner_id=user.id,
                                memory_type=MEMORY_TYPE_PREFERENCE,
                                content=event.summary,
                                source="summary",
                                confidence=event.salience,
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
                            await upsert_entity(
                                repo,
                                tenant_id,
                                user_id=user.id,
                                entity_type=str(entity.get("type") or "generic"),
                                name=name,
                                facts=event.facts or [event.summary],
                                content=event.summary,
                                source="summary",
                                confidence=event.salience,
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
                    # Keep a light pointer only when salience is high (user constraint).
                    if event.salience < 0.75:
                        continue

                embedding = await self._safe_embed(event.summary)
                expires_at = None
                if event.event_type == "conversation_fact" and event.salience < 0.7:
                    expires_at = self._ttl_expiry(
                        self.settings.MEMORY_CONVERSATION_FACT_TTL_DAYS
                    )
                written.append(
                    await write_memory(
                        repo,
                        tenant_id,
                        owner_type="user",
                        owner_id=user.id,
                        memory_type=MEMORY_TYPE_LONG_TERM,
                        content=event.summary,
                        source="summary",
                        confidence=event.salience,
                        embedding=embedding,
                        expires_at=expires_at,
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

            # Thin conversation-scoped trail for audit / backward compatibility.
            # Confidence reflects the strongest signal actually extracted this turn
            # (low for chitchat with no events), not a fixed placeholder.
            trail_confidence = max((event.salience for event in events), default=0.3)
            trail = await write_memory(
                repo,
                tenant_id,
                owner_type="conversation",
                owner_id=conversation.id,
                memory_type="conversation_summary",
                content=(
                    f"用户问题：{question}\n助手回答摘要：{answer[:500]}"
                ),
                source="summary",
                confidence=trail_confidence,
                meta_json={
                    "event_type": "conversation_fact",
                    "salience": trail_confidence,
                    "source_message_ids": source_message_ids or [],
                    "extracted_event_count": len(events),
                },
            )
            written.append(trail)
            await uow.commit()

        # Window compression runs after the memory unit of work has committed. Its
        # own memory write and the synchronous conversation-summary update are then
        # serialized rather than two open write transactions racing for the same
        # row store - which SQLite (single-writer) would deadlock on, even though
        # PostgreSQL would not.
        await self._maybe_compress_window(session, conversation, user)
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
        user: User,
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
        # Unload compressed content into LTM as a short-TTL rolling event on the
        # memory unit of work (not physical cold storage).
        rolling = str(new_summary.get("rolling_summary") or "").strip()
        if rolling and rolling != str(prev.get("rolling_summary") or "").strip():
            embedding = await self._safe_embed(rolling)
            # Unload compressed content into LTM as a short-TTL rolling event on its
            # own memory unit of work, committed before the synchronous
            # conversation-summary write below. Keeping the two writes sequential
            # rather than concurrent is what lets a single-writer store (SQLite in
            # development) avoid a write-lock deadlock; PostgreSQL is unaffected.
            async with self._unit_of_work() as uow:
                await write_memory(
                    uow.memories,
                    require_tenant(user.tenant_id, "MemoryAgent._maybe_compress_window"),
                    owner_type="conversation",
                    owner_id=conversation.id,
                    memory_type=MEMORY_TYPE_LONG_TERM,
                    content=f"会话压缩摘要：{rolling[:500]}",
                    source="summary",
                    confidence=0.55,
                    embedding=embedding,
                    expires_at=self._ttl_expiry(self.settings.MEMORY_STM_UNLOAD_TTL_DAYS),
                    meta_json={
                        "event_type": "conversation_fact",
                        "salience": 0.55,
                        "unloaded_from_stm": True,
                        "compressed_message_ids": new_summary.get("compressed_message_ids")
                        or [],
                    },
                )
                await uow.commit()
        # The rolling summary lives on the conversations table, so it stays on the
        # request's synchronous session.
        update_conversation_summary(session, conversation, new_summary)

    @staticmethod
    def _ttl_expiry(days: int) -> datetime | None:
        if days <= 0:
            return None
        return datetime.now(UTC) + timedelta(days=days)

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
    def _item_payload(
        item: MemoryItem,
        *,
        rank_score: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": item.id,
            "type": item.memory_type,
            "content": item.content,
            "confidence": item.confidence,
            "source": item.source,
            "meta": dict(item.meta_json or {}),
        }
        if rank_score is not None:
            payload["rank_score"] = float(rank_score)
        return payload

    # Minimal writeback used when the full agent is not wired (fallback path).
    # The tenant is required because ``memory_items`` is tenant-owned; a caller
    # that has a user hands its tenant in rather than letting the row go unowned.
    async def run(
        self,
        tenant_id: str,
        conversation_id: str,
        question: str,
        answer: str,
    ) -> MemoryItem:
        content = f"用户问题：{question}{chr(10)}助手回答摘要：{answer[:500]}"
        async with self._unit_of_work() as uow:
            item = await write_memory(
                uow.memories,
                tenant_id,
                owner_type="conversation",
                owner_id=conversation_id,
                memory_type="conversation_summary",
                content=content,
                source="summary",
                confidence=0.6,
                meta_json={"event_type": "conversation_fact", "legacy": True},
            )
            await uow.commit()
        return item
