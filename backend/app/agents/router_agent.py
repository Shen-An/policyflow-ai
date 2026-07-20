"""Question router: LLM structured output with heuristic fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.agents.plan_normalize import (
    difficulty_to_reasoning_mode,
    should_be_branched,
    should_be_multi_step,
)
from backend.app.db.models import KnowledgeBase
from backend.app.rag.protocols import LLMService
from backend.app.schemas.chat import PlanStep, RouterResult

_VALID_TASK_TYPES = {
    "knowledge_qa",
    "process_checklist",
    "policy_compare",
    "summary",
    "draft",
    "other",
}
_VALID_RISK = {"low", "medium", "high"}
_VALID_COMPLEXITY = {"simple", "multi_step"}
_VALID_DIFFICULTY = {"simple", "multi_step", "branched"}
_VALID_PLAN_KINDS = {"retrieve", "skill", "answer", "tool", "verify"}
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

    provisional = RouterResult(
        domain=domain,
        task_type=task_type,
        risk_level=risk_level,
        need_skill=need_skill,
        tool_hints=sorted(set(tool_hints)),
        rewrite_query=None,
        complexity="simple",
        difficulty="simple",
        reasoning_mode="cot_direct",
        plan_steps=[],
        plan_source="none",
    )
    # Light difficulty hint only; normalize_plan finalizes steps + mode.
    if should_be_branched(question, provisional):
        provisional = provisional.model_copy(
            update={
                "complexity": "multi_step",
                "difficulty": "branched",
                "reasoning_mode": "tot_select",
            }
        )
    elif should_be_multi_step(question, provisional):
        provisional = provisional.model_copy(
            update={
                "complexity": "multi_step",
                "difficulty": "multi_step",
                "reasoning_mode": "cot_steps",
            }
        )
    return provisional


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


def _coerce_plan_steps(raw: Any) -> list[PlanStep]:
    if not isinstance(raw, list):
        return []
    steps: list[PlanStep] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        kind = str(item.get("kind") or item.get("type") or "retrieve").strip().lower()
        if kind not in _VALID_PLAN_KINDS:
            kind = "retrieve"
        sid = str(item.get("id") or f"s{idx}").strip() or f"s{idx}"
        query = item.get("query")
        query_s = str(query).strip()[:4000] if isinstance(query, str) and query.strip() else None
        skill_hint = item.get("skill_hint") or item.get("skill")
        skill_s = (
            str(skill_hint).strip()[:64]
            if isinstance(skill_hint, str) and skill_hint.strip()
            else None
        )
        tool_hints_raw = item.get("tool_hints") or []
        tool_hints = (
            [str(t).strip() for t in tool_hints_raw if str(t).strip()][:8]
            if isinstance(tool_hints_raw, list)
            else []
        )
        steps.append(
            PlanStep(
                id=sid[:32],
                title=title[:120],
                kind=kind,  # type: ignore[arg-type]
                query=query_s,
                skill_hint=skill_s,
                tool_hints=tool_hints,
                status="pending",
            )
        )
        if len(steps) >= 5:
            break
    return steps


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
            '"need_skill": bool, "tool_hints": [str], "rewrite_query": str|null, '
            '"complexity": "simple"|"multi_step", '
            '"difficulty": "simple"|"multi_step"|"branched", '
            '"plan_steps": [{"id": str, "title": str, "kind": str, "query": str|null, '
            '"skill_hint": str|null, "tool_hints": [str]}]}。'
            f"domain 优先从这些知识库 code 中选：{json.dumps([k['code'] for k in kb_catalog], ensure_ascii=False)}；"
            "若不确定用 general。"
            "task_type ∈ knowledge_qa|process_checklist|policy_compare|summary|draft|other；"
            "risk_level ∈ low|medium|high。"
            "need_skill=true 当用户要清单/对比/摘要等业务规程。"
            "tool_hints 可含 skill.run,kb.search,draft.create,memory.read,mcp.call。"
            "rewrite_query：若原问题含指代/省略，给出更适合检索的完整查询；否则 null。"
            "complexity=simple：单事实问答/短跟进；"
            "complexity=multi_step：多意图、多阶段、用户已编号步骤、或需先检索再清单/对比。"
            "difficulty=simple：直答；multi_step：单链分步；"
            "difficulty=branched：存在多种合理执行路径需用户选路（如几种方案/对比路径/先A或先B）。"
            "用户已编号 1.2.3. 线性步骤时 difficulty 必须 multi_step 而非 branched。"
            "multi_step/branched 时 plan_steps 给 2-5 步主路径草案，kind ∈ retrieve|skill|answer|tool|verify；"
            "simple 时 plan_steps 必须为 []。"
            "你不是开放式 planner，也不是学术 ToT 搜索：只做结构化路由。"
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

            complexity_raw = str(parsed.get("complexity") or fallback.complexity).strip().lower()
            if complexity_raw not in _VALID_COMPLEXITY:
                complexity_raw = fallback.complexity
            difficulty_raw = str(
                parsed.get("difficulty") or getattr(fallback, "difficulty", "simple")
            ).strip().lower()
            if difficulty_raw not in _VALID_DIFFICULTY:
                difficulty_raw = getattr(fallback, "difficulty", "simple")
            plan_steps = _coerce_plan_steps(parsed.get("plan_steps"))
            if complexity_raw == "simple" and difficulty_raw == "simple":
                plan_steps = []
            # If model gave multi steps but labeled simple, promote.
            if len(plan_steps) >= 2:
                complexity_raw = "multi_step"
                if difficulty_raw == "simple":
                    difficulty_raw = "multi_step"
            if difficulty_raw == "branched":
                complexity_raw = "multi_step"
            # Align complexity with difficulty for multi_step.
            if difficulty_raw == "multi_step":
                complexity_raw = "multi_step"

            return RouterResult(
                domain=domain,
                task_type=task_type,
                risk_level=risk_level,
                need_skill=need_skill,
                tool_hints=sorted(set(tool_hints)),
                rewrite_query=rewrite_query,
                complexity=complexity_raw,  # type: ignore[arg-type]
                difficulty=difficulty_raw,  # type: ignore[arg-type]
                reasoning_mode=difficulty_to_reasoning_mode(difficulty_raw),  # type: ignore[arg-type]
                plan_steps=plan_steps,
                plan_source="router" if plan_steps else "none",
            )
        except Exception:
            return fallback
