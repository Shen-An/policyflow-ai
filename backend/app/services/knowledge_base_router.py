"""Deterministic knowledge-base candidate routing for federated retrieval."""

import re

from backend.app.db.models import KnowledgeBase

DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "hr": (
        "hr",
        "human resources",
        "人力",
        "人事",
        "员工",
        "招聘",
        "入职",
        "离职",
        "年假",
        "病假",
        "休假",
        "考勤",
        "薪资",
    ),
    "finance": (
        "finance",
        "financial",
        "财务",
        "报销",
        "费用",
        "发票",
        "付款",
        "采购",
        "预算",
        "税务",
        "住宿标准",
    ),
    "it": (
        "it",
        "information technology",
        "信息技术",
        "信息安全",
        "网络",
        "系统",
        "账号",
        "密码",
        "权限",
        "钓鱼",
        "数据安全",
    ),
    "admin": (
        "administration",
        "administrative",
        "行政",
        "印章",
        "办公",
        "会议",
        "用章",
        "差旅",
        "出差",
        "差旅申请",
        "出差申请",
    ),
    "legal": (
        "legal",
        "law",
        "法务",
        "法律",
        "合同",
        "合规",
        "诉讼",
        "隐私",
        "个人信息",
        "数据保护",
    ),
}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _score(question: str, knowledge_base: KnowledgeBase) -> int:
    text = _normalized(question)
    score = 0
    for alias in DOMAIN_ALIASES.get(knowledge_base.code, ()):
        if alias in text:
            score += 4 if len(alias) > 2 else 2
    metadata = _normalized(
        f"{knowledge_base.code} {knowledge_base.name} {knowledge_base.description}"
    )
    for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]{2,}", text):
        if len(token) >= 2 and token in metadata:
            score += 1
    return score


def select_candidate_knowledge_bases(
    question: str,
    knowledge_bases: list[KnowledgeBase],
    max_candidates: int = 2,
) -> list[KnowledgeBase]:
    """Select likely workspaces while retaining all-workspace fallback."""
    if len(knowledge_bases) <= 1:
        return knowledge_bases
    ranked = sorted(
        ((_score(question, knowledge_base), knowledge_base) for knowledge_base in knowledge_bases),
        key=lambda item: (-item[0], item[1].code),
    )
    best_score = ranked[0][0]
    if best_score <= 0:
        return knowledge_bases
    threshold = max(1, best_score // 2)
    selected = [knowledge_base for score, knowledge_base in ranked if score >= threshold][
        :max_candidates
    ]
    return selected or knowledge_bases
