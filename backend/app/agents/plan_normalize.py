"""Normalize multi-step plans from user text or Router output (service, not Agent)."""

from __future__ import annotations

import re
from typing import Any, Literal

from backend.app.schemas.chat import PlanStep, ReasoningMode, RouterResult

PlanSource = Literal["none", "user", "router", "user_selected"]
Complexity = Literal["simple", "multi_step"]
Difficulty = Literal["simple", "multi_step", "branched"]

_VALID_KINDS = {"retrieve", "skill", "answer", "tool", "verify"}
_DEFAULT_MAX_STEPS = 5

# Explicit user-numbered steps: "1. ..." / "1) ..." / "第一步 ..."
_USER_NUMBERED = re.compile(
    r"(?m)^\s*(?:(\d{1,2})[\.\)、:：]\s*|(第[一二三四五六七八九十\d]+[步阶段点])[:：\s]+)(.+)$"
)
# Inline multi-intent / multi-phase signals (Chinese + English).
_MULTI_PHASE = re.compile(
    r"(先.+再|然后|最后|并且|同时|还要|以及|对比.+清单|查.+给|检索.+生成|"
    r"first.+then|and then|also |compare.+list)",
    re.IGNORECASE,
)
_EXPLICIT_PLAN_REQUEST = re.compile(
    r"(分步|分几步|步骤执行|请规划|制定计划|拆成|一步步|step by step|break down)",
    re.IGNORECASE,
)
# Competing strategies / explicit alternatives → ToT select (branched).
_BRANCHED_HINTS = re.compile(
    r"(几种方案|多条路径|哪条路|可选路径|对比路径|两种做法|三种做法|"
    r"还是先|或者先|也可以先|备选方案|多方案|选哪|"
    r"alternative|which path|either .+ or|two approaches|multiple options)",
    re.IGNORECASE,
)


def parse_user_numbered_steps(question: str, *, max_steps: int = _DEFAULT_MAX_STEPS) -> list[PlanStep]:
    """Extract ordered steps if the user already numbered them."""
    steps: list[PlanStep] = []
    for match in _USER_NUMBERED.finditer(question or ""):
        # Groups: (1) arabic number, (2) 第N步 label, (3) title body
        title = (match.group(3) or match.group(2) or "").strip()
        if not title:
            continue
        kind = _infer_kind(title)
        steps.append(
            PlanStep(
                id=f"s{len(steps) + 1}",
                title=title[:120],
                kind=kind,  # type: ignore[arg-type]
                query=title[:4000] if kind in {"retrieve", "skill", "tool"} else None,
                skill_hint=_infer_skill_hint(title) if kind == "skill" else None,
                status="pending",
            )
        )
        if len(steps) >= max_steps:
            break
    return steps


def should_be_multi_step(
    question: str,
    router_result: RouterResult | None = None,
) -> bool:
    """Heuristic complexity gate (conservative)."""
    text = (question or "").strip()
    if not text:
        return False
    if _EXPLICIT_PLAN_REQUEST.search(text):
        return True
    if len(parse_user_numbered_steps(text)) >= 2:
        return True
    if _MULTI_PHASE.search(text) and len(text) >= 16:
        return True
    if should_be_branched(question, router_result):
        return True
    if router_result is not None:
        if router_result.task_type in {"process_checklist", "policy_compare"} and (
            len(text) >= 24 or "对比" in text or "并且" in text or "以及" in text
        ):
            return True
    return False


def should_be_branched(
    question: str,
    router_result: RouterResult | None = None,
) -> bool:
    """Heuristic ToT gate: competing strategies, not just multi-step."""
    text = (question or "").strip()
    if not text:
        return False
    # User already numbered a linear path — never force branch selection.
    if len(parse_user_numbered_steps(text)) >= 2:
        return False
    if _BRANCHED_HINTS.search(text):
        return True
    if router_result is not None and getattr(router_result, "difficulty", None) == "branched":
        return True
    # policy_compare with multi-phase phrasing often has alternative retrieve orders.
    if router_result is not None and router_result.task_type == "policy_compare":
        if _MULTI_PHASE.search(text) and len(text) >= 28:
            return True
        if ("对比" in text or "compare" in text.lower()) and (
            "清单" in text or "申请" in text or "流程" in text or "模板" in text
        ):
            return True
    return False


def difficulty_to_reasoning_mode(difficulty: Difficulty) -> ReasoningMode:
    if difficulty == "branched":
        return "tot_select"
    if difficulty == "multi_step":
        return "cot_steps"
    return "cot_direct"


def _with_mode(
    router_result: RouterResult,
    *,
    complexity: Complexity,
    difficulty: Difficulty,
    plan_steps: list[PlanStep],
    plan_source: PlanSource,
) -> RouterResult:
    return router_result.model_copy(
        update={
            "complexity": complexity,
            "difficulty": difficulty,
            "reasoning_mode": difficulty_to_reasoning_mode(difficulty),
            "plan_steps": plan_steps,
            "plan_source": plan_source,
        }
    )


def _infer_kind(text: str) -> str:
    lowered = text.lower()
    if any(tok in lowered for tok in ("对比", "清单", "流程", "步骤", "摘要", "总结", "checklist", "compare", "summary")):
        return "skill"
    if any(tok in lowered for tok in ("草稿", "邮件", "draft", "email", "mcp", "记忆", "memory")):
        return "tool"
    if any(tok in lowered for tok in ("回答", "汇总", "整理答案", "生成回答", "answer", "总结回答")):
        return "answer"
    if any(tok in lowered for tok in ("校验", "合规", "核实", "verify")):
        return "verify"
    if any(tok in lowered for tok in ("查", "检索", "搜", "标准", "制度", "search", "retrieve", "look up")):
        return "retrieve"
    return "retrieve"


def _infer_skill_hint(text: str) -> str | None:
    lowered = text.lower()
    if any(tok in lowered for tok in ("对比", "区别", "compare")):
        return "policy_compare"
    if any(tok in lowered for tok in ("摘要", "总结", "summary")):
        return "summary"
    if any(tok in lowered for tok in ("清单", "流程", "步骤", "checklist", "process")):
        return "process_checklist"
    return None


def _coerce_step(raw: Any, index: int) -> PlanStep | None:
    if isinstance(raw, PlanStep):
        step = raw
        sid = step.id or f"s{index}"
        kind = step.kind if step.kind in _VALID_KINDS else "retrieve"
        title = (step.title or "").strip() or f"步骤 {index}"
        return PlanStep(
            id=sid[:32],
            title=title[:120],
            kind=kind,  # type: ignore[arg-type]
            query=(step.query.strip()[:4000] if isinstance(step.query, str) and step.query.strip() else None),
            skill_hint=(step.skill_hint.strip()[:64] if isinstance(step.skill_hint, str) and step.skill_hint.strip() else None),
            tool_hints=[str(t).strip() for t in (step.tool_hints or []) if str(t).strip()][:8],
            depends_on=[str(d).strip() for d in (step.depends_on or []) if str(d).strip()][:8],
            status="pending",
        )
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or raw.get("name") or "").strip()
    if not title:
        return None
    kind_raw = str(raw.get("kind") or raw.get("type") or "retrieve").strip().lower()
    kind = kind_raw if kind_raw in _VALID_KINDS else _infer_kind(title)
    sid = str(raw.get("id") or f"s{index}").strip() or f"s{index}"
    query = raw.get("query")
    query_s = str(query).strip()[:4000] if isinstance(query, str) and query.strip() else None
    skill_hint = raw.get("skill_hint") or raw.get("skill")
    skill_s = str(skill_hint).strip()[:64] if isinstance(skill_hint, str) and skill_hint.strip() else None
    if kind == "skill" and not skill_s:
        skill_s = _infer_skill_hint(title)
    tool_hints_raw = raw.get("tool_hints") or []
    tool_hints = (
        [str(t).strip() for t in tool_hints_raw if str(t).strip()][:8]
        if isinstance(tool_hints_raw, list)
        else []
    )
    depends_raw = raw.get("depends_on") or raw.get("dependsOn") or []
    depends_on = (
        [str(d).strip() for d in depends_raw if str(d).strip()][:8]
        if isinstance(depends_raw, list)
        else []
    )
    return PlanStep(
        id=sid[:32],
        title=title[:120],
        kind=kind,  # type: ignore[arg-type]
        query=query_s or (title[:4000] if kind in {"retrieve", "skill", "tool"} else None),
        skill_hint=skill_s,
        tool_hints=tool_hints,
        depends_on=depends_on,
        status="pending",
    )


def _ensure_answer_tail(steps: list[PlanStep], *, max_steps: int) -> list[PlanStep]:
    if not steps:
        return steps
    if any(step.kind == "answer" for step in steps):
        return steps[:max_steps]
    if len(steps) >= max_steps:
        # Convert last step to answer if no room.
        last = steps[max_steps - 1]
        steps = steps[: max_steps - 1] + [
            PlanStep(
                id=last.id,
                title=last.title if "答" in last.title else "整理最终回答",
                kind="answer",
                status="pending",
            )
        ]
        return steps
    steps.append(
        PlanStep(
            id=f"s{len(steps) + 1}",
            title="整理最终回答",
            kind="answer",
            status="pending",
        )
    )
    return steps


def _default_plan_for_task(question: str, router_result: RouterResult) -> list[PlanStep]:
    """Build a small supervisor plan when multi_step but no explicit steps."""
    q = question.strip()[:4000]
    rewrite = (router_result.rewrite_query or "").strip() or q
    steps: list[PlanStep] = [
        PlanStep(
            id="s1",
            title="检索相关制度证据",
            kind="retrieve",
            query=rewrite,
            status="pending",
        )
    ]
    if router_result.need_skill or router_result.task_type in {
        "process_checklist",
        "policy_compare",
        "summary",
    }:
        skill = {
            "process_checklist": "process_checklist",
            "policy_compare": "policy_compare",
            "summary": "summary",
        }.get(router_result.task_type, "process_checklist")
        steps.append(
            PlanStep(
                id="s2",
                title=f"执行业务规程（{skill}）",
                kind="skill",
                query=rewrite,
                skill_hint=skill,
                tool_hints=["skill.run"],
                status="pending",
            )
        )
    steps.append(
        PlanStep(
            id=f"s{len(steps) + 1}",
            title="基于证据生成回答",
            kind="answer",
            status="pending",
        )
    )
    return steps


def normalize_plan(
    question: str,
    router_result: RouterResult,
    *,
    enabled: bool = True,
    max_steps: int = _DEFAULT_MAX_STEPS,
    tot_enabled: bool = True,
    tot_auto_trigger: bool = True,
) -> RouterResult:
    """
    Produce a validated plan on RouterResult.

    Priority:
    1. User-numbered steps → multi_step + plan_source=user (never branched)
    2. Router-provided plan_steps when complexity multi_step
    3. Heuristic multi_step / branched → default task plan + difficulty
    4. Else simple / empty plan

    difficulty=branched only when strategies compete and TOT is enabled;
    complexity stays multi_step for backward-compatible clients.
    """
    max_steps = max(1, min(int(max_steps or _DEFAULT_MAX_STEPS), 10))
    if not enabled:
        return _with_mode(
            router_result,
            complexity="simple",
            difficulty="simple",
            plan_steps=[],
            plan_source="none",
        )

    user_steps = parse_user_numbered_steps(question, max_steps=max_steps)
    if len(user_steps) >= 2:
        steps = _ensure_answer_tail(user_steps, max_steps=max_steps)
        # Explicit linear path from user → CoT steps, not ToT select.
        return _with_mode(
            router_result,
            complexity="multi_step",
            difficulty="multi_step",
            plan_steps=steps,
            plan_source="user",
        )

    # Coerce router-provided steps.
    coerced: list[PlanStep] = []
    for idx, raw in enumerate(router_result.plan_steps or [], start=1):
        step = _coerce_step(raw, idx)
        if step is not None:
            coerced.append(step)
        if len(coerced) >= max_steps:
            break

    router_says_multi = router_result.complexity == "multi_step" and len(coerced) >= 2
    heuristic_multi = should_be_multi_step(question, router_result)
    branched = bool(tot_enabled and tot_auto_trigger) and (
        getattr(router_result, "difficulty", "simple") == "branched"
        or should_be_branched(question, router_result)
    )
    # If TOT disabled, never keep branched.
    if not tot_enabled:
        branched = False

    def _multi_result(steps: list[PlanStep], source: PlanSource) -> RouterResult:
        difficulty: Difficulty = "branched" if branched else "multi_step"
        return _with_mode(
            router_result,
            complexity="multi_step",
            difficulty=difficulty,
            plan_steps=steps,
            plan_source=source,
        )

    if router_says_multi:
        steps = _ensure_answer_tail(coerced, max_steps=max_steps)
        return _multi_result(steps, "router")

    if heuristic_multi or branched:
        if len(coerced) >= 2:
            steps = _ensure_answer_tail(coerced, max_steps=max_steps)
        else:
            steps = _default_plan_for_task(question, router_result)[:max_steps]
            steps = _ensure_answer_tail(steps, max_steps=max_steps)
        return _multi_result(steps, "router")

    # Single user step is still simple unless explicit plan request.
    if _EXPLICIT_PLAN_REQUEST.search(question or "") and coerced:
        steps = _ensure_answer_tail(coerced, max_steps=max_steps)
        return _multi_result(steps, "router")

    return _with_mode(
        router_result,
        complexity="simple",
        difficulty="simple",
        plan_steps=[],
        plan_source="none",
    )


def primary_retrieve_query(plan_steps: list[PlanStep], fallback: str) -> str:
    """Pick the main retrieval query from plan steps (L1: single hybrid retrieve)."""
    for step in plan_steps:
        if step.kind == "retrieve" and step.query and step.query.strip():
            return step.query.strip()
    # Fall back to first non-answer query.
    for step in plan_steps:
        if step.kind != "answer" and step.query and step.query.strip():
            return step.query.strip()
    return fallback


def merge_plan_tool_hints(plan_steps: list[PlanStep], base: list[str] | None = None) -> list[str]:
    hints = list(base or [])
    for step in plan_steps:
        hints.extend(step.tool_hints or [])
        if step.kind == "skill":
            hints.append("skill.run")
        if step.kind == "tool" and step.query:
            # Keep generic; allowlist still soft.
            pass
    # Preserve order, unique.
    seen: set[str] = set()
    out: list[str] = []
    for item in hints:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def plan_needs_skill(plan_steps: list[PlanStep], fallback: bool) -> bool:
    if any(step.kind == "skill" for step in plan_steps):
        return True
    return fallback


def set_step_status(
    plan_steps: list[PlanStep],
    step_id: str,
    status: Literal["pending", "running", "success", "skipped", "error"],
) -> list[PlanStep]:
    updated: list[PlanStep] = []
    for step in plan_steps:
        if step.id == step_id:
            updated.append(step.model_copy(update={"status": status}))
        else:
            updated.append(step)
    return updated


def set_kind_status(
    plan_steps: list[PlanStep],
    kind: str,
    status: Literal["pending", "running", "success", "skipped", "error"],
) -> list[PlanStep]:
    updated: list[PlanStep] = []
    for step in plan_steps:
        if step.kind == kind and step.status in {"pending", "running"}:
            updated.append(step.model_copy(update={"status": status}))
        else:
            updated.append(step)
    return updated
