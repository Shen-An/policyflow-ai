"""Extract event-granularity memory candidates from a chat turn."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from backend.app.rag.protocols import LLMService

EVENT_TYPES = frozenset(
    {"preference", "decision", "todo", "entity_update", "conversation_fact"}
)

PREFERENCE_PATTERNS = (
    re.compile(r"(以后|之后|下次|今后).{0,12}(用|请|希望|偏好)"),
    re.compile(r"(请|希望|偏好|习惯).{0,12}(用|以).{0,20}(回答|回复|格式|列表|表格|要点)"),
    re.compile(r"(prefer|please always|from now on)", re.I),
)

ENTITY_PATTERNS = (
    re.compile(r"(?:我的|本人)?(?:默认)?部门是\s*([^\s，。,.；;]{1,30})"),
    re.compile(r"(?:我是|本人是)\s*([^\s，。,.；;]{1,30})"),
    re.compile(r"(?:负责|跟进)\s*([^\s，。,.；;]{1,30})"),
)

POLICY_HINTS = ("制度", "规定", "标准", "必须", "应当", "条款", "政策")


@dataclass
class MemoryEvent:
    event_type: str
    summary: str
    entities: list[dict[str, str]] = field(default_factory=list)
    salience: float = 0.5
    policy_related: bool = False
    facts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "summary": self.summary,
            "entities": self.entities,
            "salience": self.salience,
            "policy_related": self.policy_related,
            "facts": self.facts,
        }


def _clip_summary(text: str, limit: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


def _looks_policy_related(text: str) -> bool:
    return any(term in text for term in POLICY_HINTS)


def _heuristic_events(question: str, answer: str) -> list[MemoryEvent]:
    events: list[MemoryEvent] = []
    q = (question or "").strip()
    a = (answer or "").strip()

    for pattern in PREFERENCE_PATTERNS:
        if pattern.search(q):
            events.append(
                MemoryEvent(
                    event_type="preference",
                    summary=_clip_summary(f"用户偏好：{q}"),
                    salience=0.8,
                    policy_related=_looks_policy_related(q),
                )
            )
            break

    for pattern in ENTITY_PATTERNS:
        match = pattern.search(q)
        if match:
            name = match.group(1).strip("的了呢啊呀")
            if name:
                entity_type = "department" if "部门" in q else "person_or_role"
                events.append(
                    MemoryEvent(
                        event_type="entity_update",
                        summary=_clip_summary(f"实体更新：{name}"),
                        entities=[{"name": name, "type": entity_type}],
                        salience=0.75,
                        facts=[_clip_summary(q, 120)],
                        policy_related=False,
                    )
                )
            break

    # Capture a compact conversation fact only when the turn is substantive.
    if len(q) >= 8 and len(a) >= 20 and not any(
        event.event_type == "preference" for event in events
    ):
        events.append(
            MemoryEvent(
                event_type="conversation_fact",
                summary=_clip_summary(f"用户问：{q}；要点：{a[:120]}"),
                salience=0.45 if not _looks_policy_related(q) else 0.35,
                policy_related=_looks_policy_related(q) or _looks_policy_related(a),
            )
        )
    return events


def _parse_llm_events(payload: str) -> list[MemoryEvent]:
    text = (payload or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []

    events: list[MemoryEvent] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        event_type = str(raw.get("event_type") or "").strip()
        summary = _clip_summary(str(raw.get("summary") or ""))
        if event_type not in EVENT_TYPES or not summary:
            continue
        entities: list[dict[str, str]] = []
        for entity in raw.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or "").strip()
            etype = str(entity.get("type") or "generic").strip() or "generic"
            if name:
                entities.append({"name": name[:80], "type": etype[:40]})
        try:
            salience = float(raw.get("salience", 0.5))
        except (TypeError, ValueError):
            salience = 0.5
        facts = [
            _clip_summary(str(fact), 120)
            for fact in (raw.get("facts") or [])
            if str(fact).strip()
        ][:10]
        events.append(
            MemoryEvent(
                event_type=event_type,
                summary=summary,
                entities=entities,
                salience=max(0.0, min(salience, 1.0)),
                policy_related=bool(raw.get("policy_related")),
                facts=facts,
            )
        )
    return events


async def extract_memory_events(
    question: str,
    answer: str,
    *,
    llm_service: LLMService | None = None,
    existing_entity_names: list[str] | None = None,
) -> list[MemoryEvent]:
    """Return 0..N event-level memory candidates for one chat turn."""
    if llm_service is not None and getattr(llm_service, "available", True):
        entity_hint = ", ".join((existing_entity_names or [])[:12]) or "无"
        system_prompt = (
            "你是企业助手的记忆抽取器。从一轮对话中抽取值得长期记住的事件。"
            "只输出 JSON 数组，不要解释。"
            "每项字段：event_type(preference|decision|todo|entity_update|conversation_fact),"
            "summary(单句≤200字), entities([{name,type}]), salience(0-1),"
            "policy_related(bool), facts([string])."
            "规则：1) 一条事件一个粒度，不要整段对话 dump；"
            "2) 不要把制度条款正文写入 preference；"
            "3) 寒暄或无信息量则返回 []；"
            "4) 制度问答本身 policy_related=true 且仅在有用户个人约束时保留 preference。"
        )
        user_prompt = (
            f"已有实体：{entity_hint}\n"
            f"用户：{question}\n"
            f"助手：{answer[:800]}"
        )
        try:
            raw = await llm_service.complete(system_prompt, user_prompt)
            events = _parse_llm_events(raw)
            if events:
                return events
        except Exception:
            pass
    return _heuristic_events(question, answer)
