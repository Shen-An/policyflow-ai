"""Memory storage with policy-fact guards, vector search, and entity upsert.

Every function that touches the database does so through the tenant-qualified
asynchronous :class:`~backend.app.db.repositories.MemoryItemRepository`. Memory is
*not* authoritative - it never overrides the evidence retrieved for the current
turn - but it is still tenant-owned data, so ``tenant_id`` is required on every
read and write rather than an optional filter. The legacy synchronous service
filtered by owner alone, which let two tenants that shared an owner id read each
other's memories, and its inserts omitted ``tenant_id`` entirely, which the
enforced PostgreSQL schema rejects outright. Both defects are closed by routing
through the repository with a mandatory tenant.

The pure ranking helpers (cosine similarity, keyword overlap, the fused rank
score) carry no database dependency and stay synchronous so they remain trivially
testable.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterable
from datetime import UTC, datetime
from typing import Any

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import MemoryItem
from backend.app.db.repositories import MemoryItemRepository

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


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    Score is request-scoped only - never persisted to the row.
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


async def write_memory(
    repo: MemoryItemRepository,
    tenant_id: str,
    *,
    owner_type: str,
    owner_id: str,
    memory_type: str,
    content: str,
    source: str = "manual",
    confidence: float = 0.5,
    embedding: list[float] | None = None,
    meta_json: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> MemoryItem:
    """Persist one memory for ``tenant_id`` after the policy-fact guard.

    A user preference may not smuggle a policy statement into memory, because a
    preference outranks nothing and must never be mistaken for authoritative
    policy. The tenant is required and stamped on the row by the repository.
    """
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
    return await repo.create(
        tenant_id,
        owner_type=owner_type,
        owner_id=owner_id,
        memory_type=memory_type,
        content=cleaned,
        source=source,
        confidence=confidence,
        embedding=embedding,
        meta_json=dict(meta_json or {}),
        expires_at=expires_at,
    )


async def read_memory(
    repo: MemoryItemRepository,
    tenant_id: str,
    owner_type: str,
    owner_id: str,
    *,
    memory_types: Iterable[str] | None = None,
) -> list[MemoryItem]:
    """Return this tenant's live memories for one owner (expired rows excluded)."""
    types = list(memory_types) if memory_types is not None else None
    return await repo.list_for_owner(
        tenant_id, owner_type, owner_id, memory_types=types
    )


async def list_fixed_memories(
    repo: MemoryItemRepository,
    tenant_id: str,
    user_id: str,
    *,
    prefs_limit: int = 10,
    entity_limit: int = 8,
) -> list[MemoryItem]:
    """Always-on user preferences and high-confidence entities for one member."""

    def _by_confidence(items: list[MemoryItem]) -> list[MemoryItem]:
        return sorted(
            items,
            key=lambda item: (
                item.confidence if item.confidence is not None else 0.0,
                item.updated_at or item.created_at,
            ),
            reverse=True,
        )

    prefs = _by_confidence(
        await repo.list_for_owner(
            tenant_id, "user", user_id, memory_types=[MEMORY_TYPE_PREFERENCE]
        )
    )[: max(prefs_limit, 0)]
    entities = _by_confidence(
        await repo.list_for_owner(
            tenant_id, "user", user_id, memory_types=[MEMORY_TYPE_ENTITY]
        )
    )[: max(entity_limit, 0)]
    return prefs + entities


async def search_memories_scored(
    repo: MemoryItemRepository,
    tenant_id: str,
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
    """Search this tenant's memories and return (rank_score, item), highest first.

    Every owner spec is qualified by ``tenant_id`` at the repository, so an owner
    id that belongs to another tenant cannot pull that tenant's rows into the
    result.
    """
    if top_k <= 0 or not owner_specs:
        return []
    clock = _as_utc(now) or datetime.now(UTC)
    types = list(memory_types) if memory_types is not None else list(SEARCHABLE_TYPES)
    candidates: list[MemoryItem] = []
    seen: set[str] = set()
    for owner_type, owner_id in owner_specs:
        rows = await repo.list_for_owner(
            tenant_id, owner_type, owner_id, memory_types=types, now=clock
        )
        for item in rows:
            if item.id in seen:
                continue
            seen.add(item.id)
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


async def search_memories(
    repo: MemoryItemRepository,
    tenant_id: str,
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
    scored = await search_memories_scored(
        repo,
        tenant_id,
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


async def touch_access(
    repo: MemoryItemRepository,
    tenant_id: str,
    items: Iterable[MemoryItem],
    *,
    now: datetime | None = None,
) -> None:
    """Record that ``items`` were recalled, so access heat feeds future ranking."""
    await repo.record_access(tenant_id, list(items), now=now)


async def upsert_entity(
    repo: MemoryItemRepository,
    tenant_id: str,
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
    """Create or merge one entity memory for this tenant's member.

    A matching entity is found within the tenant's own rows and merged in place;
    the mutated row persists when the caller's unit of work commits. A new entity
    is created through :func:`write_memory`, which flushes immediately.
    """
    cleaned_name = (name or "").strip()
    cleaned_type = (entity_type or "generic").strip() or "generic"
    if not cleaned_name:
        raise ApplicationError("VALIDATION_ERROR", "Entity name is required", 422)
    entity_key = f"{cleaned_type}:{cleaned_name}".lower()
    existing = await repo.list_for_owner(
        tenant_id, "user", user_id, memory_types=[MEMORY_TYPE_ENTITY]
    )
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
        return await write_memory(
            repo,
            tenant_id,
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
    await repo.record_access(tenant_id, [matched])
    return matched


async def find_similar_preference(
    repo: MemoryItemRepository,
    tenant_id: str,
    user_id: str,
    content: str,
) -> MemoryItem | None:
    """Return an existing preference that is essentially the same statement."""
    normalized = re.sub(r"\s+", "", content.lower())
    if not normalized:
        return None
    for item in await read_memory(
        repo,
        tenant_id,
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


MANAGEABLE_TYPES: Collection[str] = (
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


async def list_user_memories(
    repo: MemoryItemRepository,
    tenant_id: str,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    memory_type: str | None = None,
    keyword: str | None = None,
    include_expired: bool = False,
) -> tuple[list[MemoryItem], int]:
    """List a member's memories (user-owned plus their conversation trail).

    Every query the repository issues is qualified by ``tenant_id``, so a
    conversation or owner id belonging to another tenant can never surface its
    rows here.
    """
    return await repo.list_for_user(
        tenant_id,
        user_id,
        allowed_types=MANAGEABLE_TYPES,
        page=page,
        page_size=page_size,
        memory_type=memory_type,
        keyword=keyword,
        include_expired=include_expired,
    )


async def get_user_memory(
    repo: MemoryItemRepository,
    tenant_id: str,
    user_id: str,
    memory_id: str,
) -> MemoryItem:
    """Return one memory the member may manage, or a not-found error."""
    return await repo.get_for_user(tenant_id, user_id, memory_id)


async def delete_user_memory(
    repo: MemoryItemRepository,
    tenant_id: str,
    user_id: str,
    memory_id: str,
) -> None:
    """Delete one memory the member may manage (user-owned or their conversation)."""
    await repo.delete_for_user(tenant_id, user_id, memory_id)
