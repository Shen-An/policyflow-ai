"""Question router: LLM structured output with heuristic fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.db.models import KnowledgeBase
from backend.app.rag.protocols import LLMService
from backend.app.schemas.chat import RouterResult

_VALID_TASK_TYPES = {
    "knowledge_qa",
    "process_checklist",
    "policy_compare",
    "summary",
    "draft",
    "other",
}
_VALID_RISK = {"low", "medium", "high"}
_SKILL_TASKS = {"process_checklist", "policy_compare", "summary"}


def _heuristic_route(question: str, knowledge_bases: list[KnowledgeBase]) -> RouterResult:
    lowered = question.lower()
    if len(knowledge_bases) == 1:
        domain = knowledge_bases[0].code
    else:
        domain = "general"
        for kb in knowledge_bases:
            if kb.code.lower() in lowered or kb.name.lower() in lowered:
                domain = kb.code
                break

    if any(term in lowered for term in ("流程", "步骤", "清单", "process", "travel", "差旅")):
        task_type = "process_checklist"
    elif any(term in lowered for term in ("对比", "区别", "compare")):
        task_type = "policy_compare"
    elif any(term in lowered for term in ("总结", "摘要", "summary")):
        task_type = "summary"
    elif any(term in lowered for term in ("草稿", "邮件", "draft", "email")):
        task_type = "draft"
    else:
        task_type = "knowledge_qa"

    if any(term in lowered for term in ("法律", "合规", "处罚", "诉讼", "违法")):
        risk_level = "high"
    elif any(term in lowered for term in ("财务", "报销", "预算", "薪资")):
        risk_level = "medium"
    else:
        risk_level = "low"

    need_skill = task_type in _SKILL_TASKS
    tool_hints: list[str] = []
    if need_skill:
        tool_hints.append("skill.run")
    if any(term in lowered for term in ("再查", "补充检索", "search", "检索")):
        tool_hints.append("kb.search")
    if task_type == "draft" or any(term in lowered for term in ("草稿", "邮件")):
        tool_hints.append("draft.create")

    return RouterResult(
        domain=domain,
        task_type=task_type,
        risk_level=risk_level,
        need_skill=need_skill,
        tool_hints=sorted(set(tool_hints)),
        rewrite_query=None,
    )


def _parse_router_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return default


class RouterAgent:
    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service

    async def run(self, question: str, knowledge_bases: list[KnowledgeBase]) -> RouterResult:
        fallback = _heuristic_route(question, knowledge_bases)
        if self.llm_service is None:
            return fallback

        kb_catalog = [
            {"code": kb.code, "name": kb.name}
            for kb in knowledge_bases
        ] or [{"code": "general", "name": "general"}]
        system = (
            "你是企业制度问答路由器。只输出 JSON："
            '{"domain": str, "task_type": str, "risk_level": str, '
            '"need_skill": bool, "tool_hints": [str], "rewrite_query": str|null}。'
            f"domain 优先从这些知识库 code 中选：{json.dumps([k['code'] for k in kb_catalog], ensure_ascii=False)}；"
            "若不确定用 general。"
            "task_type ∈ knowledge_qa|process_checklist|policy_compare|summary|draft|other；"
            "risk_level ∈ low|medium|high。"
            "need_skill=true 当用户要清单/对比/摘要等业务规程。"
            "tool_hints 可含 skill.run,kb.search,draft.create,memory.read,mcp.call。"
            "rewrite_query：若原问题含指代/省略，给出更适合检索的完整查询；否则 null。"
            "不要解释。"
        )
        user = f"问题：{question}\n可用知识库：{json.dumps(kb_catalog, ensure_ascii=False)}"
        try:
            raw = await self.llm_service.complete(system, user)
            parsed = _parse_router_json(raw)
            if parsed is None:
                return fallback
            domain = str(parsed.get("domain") or fallback.domain).strip() or fallback.domain
            allowed_domains = {kb.code for kb in knowledge_bases} | {"general"}
            if domain not in allowed_domains:
                domain = fallback.domain
            task_type = str(parsed.get("task_type") or fallback.task_type).strip()
            if task_type not in _VALID_TASK_TYPES:
                task_type = fallback.task_type
            risk_level = str(parsed.get("risk_level") or fallback.risk_level).strip().lower()
            if risk_level not in _VALID_RISK:
                risk_level = fallback.risk_level
            need_skill = _coerce_bool(
                parsed.get("need_skill"),
                task_type in _SKILL_TASKS or fallback.need_skill,
            )
            if task_type in _SKILL_TASKS:
                need_skill = True
            tool_hints_raw = parsed.get("tool_hints") or fallback.tool_hints
            tool_hints = [
                str(item).strip()
                for item in tool_hints_raw
                if str(item).strip()
            ] if isinstance(tool_hints_raw, list) else list(fallback.tool_hints)
            rewrite = parsed.get("rewrite_query")
            rewrite_query = None
            if isinstance(rewrite, str):
                cleaned = rewrite.strip()
                if cleaned and cleaned.lower() not in {"null", "none", "n/a"}:
                    # Avoid pointless rewrites identical to the original question.
                    if cleaned != question.strip():
                        # Guard: do not invent a clean topical query from gibberish.
                        # If original is mostly non-CJK nonsense and rewrite is much
                        # "cleaner", discard rewrite so retrieval stays honest.
                        original_alnum = re.findall(r"[A-Za-z0-9]{3,}", question)
                        rewrite_alnum = re.findall(r"[A-Za-z0-9]{3,}", cleaned)
                        original_cjk = re.findall(r"[一-鿿]", question)
                        looks_gibberish = (
                            len(original_cjk) < 2
                            and len(original_alnum) >= 2
                            and any(
                                term.lower()
                                in {
                                    "zzzz",
                                    "unknown",
                                    "unmatched",
                                    "asdf",
                                    "qwerty",
                                    "galaxy",
                                }
                                for term in original_alnum
                            )
                        )
                        if looks_gibberish:
                            rewrite_query = None
                        else:
                            rewrite_query = cleaned[:4000]
            return RouterResult(
                domain=domain,
                task_type=task_type,
                risk_level=risk_level,
                need_skill=need_skill,
                tool_hints=sorted(set(tool_hints)),
                rewrite_query=rewrite_query,
            )
        except Exception:
            return fallback
