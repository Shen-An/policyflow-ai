"""Memory storage with policy-fact guards, vector search, and entity upsert."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import MemoryItem, utc_now

POLICY_FACT_TERMS = ("制度", "规定", "标准", "必须", "应当", "policy requires")

MEMORY_TYPE_PREFERENCE = "user_preference"
MEMORY_TYPE_LONG_TERM = "long_term_event"
MEMORY_TYPE_ENTITY = "entity"
MEMORY_TYPE_STM_SUMMARY = "stm_summary"
MEMORY_TYPE_CONVERSATION_SUMMARY = "conversation_summary"
MEMORY_TYPE_SYSTEM_NOTE = "system_note"

SEARCHABLE_TYPES = (
    MEMORY_TYPE_LONG_TERM,
    MEMORY_TYPE_ENTITY,
    MEMORY_TYPE_PREFERENCE,
    MEMORY_TYPE_CONVERSATION_SUMMARY,
)


def _is_active(item: MemoryItem, now: datetime) -> bool:
    if item.expires_at is None:
        return True
    expires = item.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > now


def write_memory(
    session: Session,
    owner_type: str,
    owner_id: str,
    memory_type: str,
    content: str,
    source: str = "manual",
    confidence: float = 0.5,
    *,
    embedding: list[float] | None = None,
    meta_json: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> MemoryItem:
    cleaned = (content or "").strip()
    if not cleaned:
        raise ApplicationError("VALIDATION_ERROR", "Memory content is required", 422)
    if memory_type == MEMORY_TYPE_PREFERENCE and any(
        term in cleaned.lower() for term in POLICY_FACT_TERMS
    ):
        raise ApplicationError(
            "MEMORY_POLICY_FACT_FORBIDDEN",
            "Policy facts cannot be stored as user preferences",
            422,
        )
    item = MemoryItem(
        owner_type=owner_type,
        owner_id=owner_id,
        memory_type=memory_type,
        content=cleaned[:2000],
        source=source,
        confidence=max(0.0, min(confidence, 1.0)),
        embedding=embedding,
        meta_json=dict(meta_json or {}),
        expires_at=expires_at,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def read_memory(
    session: Session,
    owner_type: str,
    owner_id: str,
    *,
    memory_types: Iterable[str] | None = None,
) -> list[MemoryItem]:
    now = datetime.now(UTC)
    statement = select(MemoryItem).where(
        MemoryItem.owner_type == owner_type,
        MemoryItem.owner_id == owner_id,
    )
    if memory_types is not None:
        allowed = list(memory_types)
        if allowed:
            statement = statement.where(col(MemoryItem.memory_type).in_(allowed))
    items = session.exec(statement.order_by(col(MemoryItem.updated_at).desc())).all()
    return [item for item in items if _is_active(item, now)]


def list_fixed_memories(
    session: Session,
    user_id: str,
    *,
    prefs_limit: int = 10,
    entity_limit: int = 8,
) -> list[MemoryItem]:
    """Always-on user preferences and high-confidence entities."""
    now = datetime.now(UTC)
    prefs = [
        item
        for item in session.exec(
            select(MemoryItem)
            .where(
                MemoryItem.owner_type == "user",
                MemoryItem.owner_id == user_id,
                MemoryItem.memory_type == MEMORY_TYPE_PREFERENCE,
            )
            .order_by(col(MemoryItem.confidence).desc(), col(MemoryItem.updated_at).desc())
        ).all()
        if _is_active(item, now)
    ][: max(prefs_limit, 0)]

    entities = [
        item
        for item in session.exec(
            select(MemoryItem)
            .where(
                MemoryItem.owner_type == "user",
                MemoryItem.owner_id == user_id,
                MemoryItem.memory_type == MEMORY_TYPE_ENTITY,
            )
            .order_by(col(MemoryItem.confidence).desc(), col(MemoryItem.updated_at).desc())
        ).all()
        if _is_active(item, now)
    ][: max(entity_limit, 0)]
    return prefs + entities


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def _keyword_score(query: str, content: str) -> float:
    tokens = [token for token in re.split(r"\s+|[,，。；;：:、/|]+", query.lower()) if token]
    if not tokens:
        return 0.0
    haystack = content.lower()
    hits = sum(1 for token in tokens if token in haystack)
    return hits / len(tokens)


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def memory_rank_score(
    item: MemoryItem,
    *,
    query: str,
    query_embedding: list[float] | None = None,
    now: datetime | None = None,
    decay_lambda: float = 0.08,
    access_boost_cap: float = 0.15,
) -> float:
    """Fuse relevance, importance, recency, and access heat into one rank score.

    final = relevance * (0.55 + 0.35 * importance + 0.10 * recency) + access_boost
    Score is request-scoped only — never persisted to the row.
    """
    vector_score = 0.0
    if query_embedding and item.embedding:
        vector_score = cosine_similarity(query_embedding, item.embedding)
    keyword = _keyword_score(query, item.content)
    relevance = max(vector_score, keyword * 0.85)
    if relevance <= 0.0:
        return 0.0

    meta = item.meta_json or {}
    confidence = _clamp01(item.confidence if item.confidence is not None else 0.5)
    raw_salience = meta.get("salience")
    try:
        salience = _clamp01(float(raw_salience)) if raw_salience is not None else confidence
    except (TypeError, ValueError):
        salience = confidence
    importance = _clamp01(0.5 * confidence + 0.5 * salience)

    clock = _as_utc(now) or datetime.now(UTC)
    anchor = _as_utc(item.updated_at) or _as_utc(item.created_at) or clock
    age_hours = max(0.0, (clock - anchor).total_seconds() / 3600.0)
    lam = max(0.0, float(decay_lambda))
    recency = math.exp(-lam * age_hours / 24.0)

    try:
        access_count = max(0, int(meta.get("access_count") or 0))
    except (TypeError, ValueError):
        access_count = 0
    access_boost = min(max(0.0, float(access_boost_cap)), math.log1p(access_count) * 0.03)

    return relevance * (0.55 + 0.35 * importance + 0.10 * recency) + access_boost


def search_memories_scored(
    session: Session,
    *,
    owner_specs: list[tuple[str, str]],
    query: str,
    query_embedding: list[float] | None = None,
    memory_types: Iterable[str] | None = None,
    top_k: int = 5,
    now: datetime | None = None,
    decay_lambda: float = 0.08,
    access_boost_cap: float = 0.15,
) -> list[tuple[float, MemoryItem]]:
    """Search memories and return (rank_score, item) pairs, highest first."""
    if top_k <= 0 or not owner_specs:
        return []
    clock = _as_utc(now) or datetime.now(UTC)
    types = list(memory_types) if memory_types is not None else list(SEARCHABLE_TYPES)
    candidates: list[MemoryItem] = []
    for owner_type, owner_id in owner_specs:
        statement = select(MemoryItem).where(
            MemoryItem.owner_type == owner_type,
            MemoryItem.owner_id == owner_id,
        )
        if types:
            statement = statement.where(col(MemoryItem.memory_type).in_(types))
        for item in session.exec(statement).all():
            if _is_active(item, clock):
                candidates.append(item)

    scored: list[tuple[float, MemoryItem]] = []
    for item in candidates:
        score = memory_rank_score(
            item,
            query=query,
            query_embedding=query_embedding,
            now=clock,
            decay_lambda=decay_lambda,
            access_boost_cap=access_boost_cap,
        )
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].confidence), reverse=True)
    return scored[:top_k]


def search_memories(
    session: Session,
    *,
    owner_specs: list[tuple[str, str]],
    query: str,
    query_embedding: list[float] | None = None,
    memory_types: Iterable[str] | None = None,
    top_k: int = 5,
    now: datetime | None = None,
    decay_lambda: float = 0.08,
    access_boost_cap: float = 0.15,
) -> list[MemoryItem]:
    """Search memories by fused relevance / importance / recency ranking."""
    scored = search_memories_scored(
        session,
        owner_specs=owner_specs,
        query=query,
        query_embedding=query_embedding,
        memory_types=memory_types,
        top_k=top_k,
        now=now,
        decay_lambda=decay_lambda,
        access_boost_cap=access_boost_cap,
    )
    return [item for _, item in scored]


def touch_access(session: Session, items: Iterable[MemoryItem]) -> None:
    now = utc_now()
    changed = False
    for item in items:
        meta = dict(item.meta_json or {})
        meta["last_accessed_at"] = now.isoformat()
        meta["access_count"] = int(meta.get("access_count") or 0) + 1
        item.meta_json = meta
        item.updated_at = now
        session.add(item)
        changed = True
    if changed:
        session.commit()


def upsert_entity(
    session: Session,
    *,
    user_id: str,
    entity_type: str,
    name: str,
    facts: list[str] | None = None,
    content: str | None = None,
    source: str = "summary",
    confidence: float = 0.7,
    embedding: list[float] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> MemoryItem:
    cleaned_name = (name or "").strip()
    cleaned_type = (entity_type or "generic").strip() or "generic"
    if not cleaned_name:
        raise ApplicationError("VALIDATION_ERROR", "Entity name is required", 422)
    entity_key = f"{cleaned_type}:{cleaned_name}".lower()
    existing = session.exec(
        select(MemoryItem).where(
            MemoryItem.owner_type == "user",
            MemoryItem.owner_id == user_id,
            MemoryItem.memory_type == MEMORY_TYPE_ENTITY,
        )
    ).all()
    matched: MemoryItem | None = None
    for item in existing:
        meta = item.meta_json or {}
        if meta.get("entity_key") == entity_key or (
            meta.get("entity_type") == cleaned_type and meta.get("entity_name") == cleaned_name
        ):
            matched = item
            break

    merged_facts: list[str] = []
    if matched is not None:
        previous = list((matched.meta_json or {}).get("facts") or [])
        merged_facts.extend(str(fact) for fact in previous if str(fact).strip())
    for fact in facts or []:
        cleaned_fact = str(fact).strip()
        if cleaned_fact and cleaned_fact not in merged_facts:
            merged_facts.append(cleaned_fact)
    merged_facts = merged_facts[-20:]

    summary = (content or "").strip()
    if not summary:
        if merged_facts:
            summary = f"{cleaned_name}（{cleaned_type}）：" + "；".join(merged_facts[:5])
        else:
            summary = f"{cleaned_name}（{cleaned_type}）"
    summary = summary[:2000]

    meta = {
        "entity_key": entity_key,
        "entity_type": cleaned_type,
        "entity_name": cleaned_name,
        "facts": merged_facts,
        **dict(extra_meta or {}),
    }
    if matched is None:
        return write_memory(
            session,
            owner_type="user",
            owner_id=user_id,
            memory_type=MEMORY_TYPE_ENTITY,
            content=summary,
            source=source,
            confidence=confidence,
            embedding=embedding,
            meta_json=meta,
        )

    matched.content = summary
    matched.source = source
    matched.confidence = max(matched.confidence, max(0.0, min(confidence, 1.0)))
    if embedding is not None:
        matched.embedding = embedding
    matched.meta_json = {**(matched.meta_json or {}), **meta}
    matched.updated_at = utc_now()
    session.add(matched)
    session.commit()
    session.refresh(matched)
    return matched


def find_similar_preference(
    session: Session,
    user_id: str,
    content: str,
) -> MemoryItem | None:
    """Return an existing preference that is essentially the same statement."""
    normalized = re.sub(r"\s+", "", content.lower())
    if not normalized:
        return None
    for item in read_memory(
        session,
        "user",
        user_id,
        memory_types=[MEMORY_TYPE_PREFERENCE],
    ):
        existing = re.sub(r"\s+", "", item.content.lower())
        if not existing:
            continue
        if normalized == existing or normalized in existing or existing in normalized:
            return item
    return None


MANAGEABLE_TYPES = (
    MEMORY_TYPE_PREFERENCE,
    MEMORY_TYPE_LONG_TERM,
    MEMORY_TYPE_ENTITY,
    MEMORY_TYPE_CONVERSATION_SUMMARY,
    MEMORY_TYPE_STM_SUMMARY,
    MEMORY_TYPE_SYSTEM_NOTE,
)


def to_memory_read(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "owner_type": item.owner_type,
        "owner_id": item.owner_id,
        "memory_type": item.memory_type,
        "content": item.content,
        "source": item.source,
        "confidence": item.confidence,
        "meta_json": dict(item.meta_json or {}),
        "has_embedding": bool(item.embedding),
        "expires_at": item.expires_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def list_user_memories(
    session: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    memory_type: str | None = None,
    keyword: str | None = None,
    include_expired: bool = False,
) -> tuple[list[MemoryItem], int]:
    """List memories owned by the user (user-scoped + conversation-scoped trail)."""
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), 100)
    now = datetime.now(UTC)

    # User-owned memories.
    user_items = list(
        session.exec(
            select(MemoryItem)
            .where(
                MemoryItem.owner_type == "user",
                MemoryItem.owner_id == user_id,
            )
            .order_by(col(MemoryItem.updated_at).desc())
        ).all()
    )

    # Conversation-scoped summaries for this user's conversations (optional trail).
    from backend.app.db.models import Conversation

    conversation_ids = [
        conversation.id
        for conversation in session.exec(
            select(Conversation).where(Conversation.user_id == user_id)
        ).all()
    ]
    conversation_items: list[MemoryItem] = []
    if conversation_ids:
        conversation_items = list(
            session.exec(
                select(MemoryItem)
                .where(
                    MemoryItem.owner_type == "conversation",
                    col(MemoryItem.owner_id).in_(conversation_ids),
                )
                .order_by(col(MemoryItem.updated_at).desc())
            ).all()
        )

    items = [*user_items, *conversation_items]
    # Stable de-dupe by id, keep first (already time-ordered within groups).
    seen: set[str] = set()
    unique: list[MemoryItem] = []
    for item in sorted(items, key=lambda row: row.updated_at or row.created_at, reverse=True):
        if item.id in seen:
            continue
        seen.add(item.id)
        unique.append(item)

    filtered: list[MemoryItem] = []
    type_filter = (memory_type or "").strip()
    keyword_filter = (keyword or "").strip().lower()
    for item in unique:
        if type_filter and item.memory_type != type_filter:
            continue
        if not include_expired and not _is_active(item, now):
            continue
        if keyword_filter and keyword_filter not in item.content.lower():
            continue
        if item.memory_type not in MANAGEABLE_TYPES and type_filter != item.memory_type:
            # Still show known types; ignore unknown unless explicitly filtered.
            continue
        filtered.append(item)

    total = len(filtered)
    start = (safe_page - 1) * safe_page_size
    return filtered[start : start + safe_page_size], total


def get_user_memory(session: Session, user_id: str, memory_id: str) -> MemoryItem:
    item = session.get(MemoryItem, memory_id)
    if item is None:
        raise ApplicationError("MEMORY_NOT_FOUND", "Memory not found", 404)
    if item.owner_type == "user" and item.owner_id == user_id:
        return item
    if item.owner_type == "conversation":
        from backend.app.db.models import Conversation

        conversation = session.get(Conversation, item.owner_id)
        if conversation is not None and conversation.user_id == user_id:
            return item
    raise ApplicationError("PERMISSION_DENIED", "Memory access denied", 403)


def delete_user_memory(session: Session, user_id: str, memory_id: str) -> None:
    item = get_user_memory(session, user_id, memory_id)
    session.delete(item)
    session.commit()
