"""Conversation-aware query rewriting for multi-turn retrieval."""

from __future__ import annotations

import re
from typing import Any

# Short / deictic follow-ups that usually need prior-turn context.
_FOLLOWUP_MARKERS = (
    "那",
    "那这个",
    "那个",
    "这个",
    "上面",
    "刚才",
    "继续",
    "还有",
    "模板",
    "表单",
    "样例",
    "示例",
    "怎么写",
    "怎么填",
    "详细一点",
    "再详细",
    "展开说说",
    "格式",
    "表格",
)

_TOPIC_HINTS = (
    "报销",
    "差旅",
    "请假",
    "入职",
    "离职",
    "合同",
    "采购",
    "付款",
    "发票",
    "预算",
    "考勤",
    "权限",
    "账号",
    "印章",
    "合规",
    "制度",
    "流程",
    "申请",
    "审批",
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _recent_user_turns(history: list[dict[str, Any]], *, limit: int = 4) -> list[str]:
    turns: list[str] = []
    for item in reversed(history or []):
        if str(item.get("role") or "") != "user":
            continue
        content = _clean(str(item.get("content") or ""))
        if content:
            turns.append(content)
        if len(turns) >= limit:
            break
    turns.reverse()
    return turns


def is_followup_question(question: str) -> bool:
    text = _clean(question)
    if not text:
        return False
    # Self-contained questions with domain terms are not mere follow-ups.
    topics = extract_topic_terms(text)
    if topics and any(
        marker in text
        for marker in ("怎么", "如何", "哪些", "什么", "多少", "是否", "能否", "流程", "标准", "要求")
    ):
        # Exception: "模板/表单" still needs prior topic even if it contains 怎么.
        if not any(token in text for token in ("模板", "表单", "样例", "示例", "怎么填", "怎么写")):
            return False
    # Very short utterances without domain anchors are usually deictic follow-ups.
    if len(text) <= 12 and not topics:
        return True
    if text.startswith(("那", "那这个", "那个", "这个", "上面", "刚才", "继续", "还有")):
        return True
    if text.startswith(("再", "请", "麻烦")) and any(
        token in text for token in ("给", "说", "写", "列", "补", "展开", "详细")
    ):
        return True
    if any(marker in text for marker in ("模板", "表单", "样例", "示例", "怎么写", "怎么填", "详细一点", "再详细")):
        return True
    if len(text) <= 8:
        return True
    return False


def extract_topic_terms(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        lowered = text or ""
        for hint in _TOPIC_HINTS:
            if hint in lowered and hint not in found:
                found.append(hint)
    return found


def expand_retrieval_query(
    question: str,
    *,
    history: list[dict[str, Any]] | None = None,
    rolling_summary: str | None = None,
) -> str:
    """Expand short follow-ups with prior-turn topic so retrieval stays on-topic.

    Example:
      history: "怎么报销"
      question: "给我模板"
      => "给我模板 怎么报销 报销 流程 材料 附件 字段 填写 清单"
    """
    current = _clean(question)
    if not current:
        return current

    history = history or []
    prior_users = _recent_user_turns(history)
    if not prior_users and not rolling_summary:
        return current

    current_topics = extract_topic_terms(current)
    wants_template = any(
        token in current for token in ("模板", "表单", "样例", "示例", "怎么填", "怎么写")
    )

    # Independent, self-contained domain questions keep original wording.
    if current_topics and not wants_template and not is_followup_question(current):
        return current

    if not is_followup_question(current) and not wants_template:
        return current

    prior_topics = extract_topic_terms(*(prior_users[-3:] + [rolling_summary or ""]))
    prior_anchor = prior_users[-1] if prior_users else _clean(rolling_summary or "")

    extras: list[str] = []
    if prior_anchor and prior_anchor not in current:
        extras.append(prior_anchor[:80])
    for term in prior_topics:
        blob = " ".join(extras)
        if term not in current and term not in blob:
            extras.append(term)

    if wants_template:
        for booster in ("流程", "材料", "附件", "字段", "填写", "清单"):
            blob = " ".join(extras)
            if booster not in current and booster not in blob:
                extras.append(booster)

    if not extras:
        return current
    return _clean(f"{current} {' '.join(extras)}")
