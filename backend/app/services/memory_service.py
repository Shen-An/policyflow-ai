"""Memory storage with policy-fact and expiry safeguards."""

from datetime import UTC, datetime

from sqlmodel import Session, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import MemoryItem

POLICY_FACT_TERMS = ("制度", "规定", "标准", "必须", "应当", "policy requires")


def write_memory(
    session: Session,
    owner_type: str,
    owner_id: str,
    memory_type: str,
    content: str,
    source: str = "manual",
    confidence: float = 0.5,
) -> MemoryItem:
    if memory_type == "user_preference" and any(term in content.lower() for term in POLICY_FACT_TERMS):
        raise ApplicationError(
            "MEMORY_POLICY_FACT_FORBIDDEN",
            "Policy facts cannot be stored as user preferences",
            422,
        )
    item = MemoryItem(
        owner_type=owner_type,
        owner_id=owner_id,
        memory_type=memory_type,
        content=content,
        source=source,
        confidence=max(0.0, min(confidence, 1.0)),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def read_memory(session: Session, owner_type: str, owner_id: str) -> list[MemoryItem]:
    now = datetime.now(UTC)
    items = session.exec(
        select(MemoryItem).where(
            MemoryItem.owner_type == owner_type,
            MemoryItem.owner_id == owner_id,
        )
    ).all()
    return [item for item in items if item.expires_at is None or item.expires_at > now]
