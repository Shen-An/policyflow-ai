"""Generate multi-candidate plan options for ToT-style user selection (service, not Agent).

Honest boundary: product "ToT" = 2–3 alternative execution plans + user pick.
Not academic Tree-of-Thoughts search / beam decoding / peer multi-agent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.agents.plan_normalize import (
    _coerce_step,
    _default_plan_for_task,
    _ensure_answer_tail,
    _infer_skill_hint,
)
from backend.app.rag.protocols import LLMService
from backend.app.schemas.chat import PlanOption, PlanStep, RouterResult

_DEFAULT_MAX_OPTIONS = 3
_DEFAULT_MIN_OPTIONS = 2
_DEFAULT_MAX_STEPS = 5


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = (text or "").strip()
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


def _normalize_option_steps(
    raw_steps: Any,
    *,
    question: str,
    router_result: RouterResult,
    max_steps: int,
) -> list[PlanStep]:
    coerced: list[PlanStep] = []
    if isinstance(raw_steps, list):
        for idx, raw in enumerate(raw_steps, start=1):
            step = _coerce_step(raw, idx)
            if step is not None:
                # Re-id per option later; keep local ids for now.
                coerced.append(step)
            if len(coerced) >= max_steps:
                break
    if len(coerced) < 2:
        coerced = _default_plan_for_task(question, router_result)[:max_steps]
    return _ensure_answer_tail(coerced, max_steps=max_steps)


def _reindex_steps(steps: list[PlanStep], option_id: str) -> list[PlanStep]:
    out: list[PlanStep] = []
    id_map: dict[str, str] = {}
    for idx, step in enumerate(steps, start=1):
        new_id = f"{option_id}_s{idx}"
        id_map[step.id] = new_id
        out.append(step.model_copy(update={"id": new_id, "status": "pending"}))
    remapped: list[PlanStep] = []
    for step in out:
        deps = [id_map.get(d, d) for d in (step.depends_on or []) if d]
        remapped.append(step.model_copy(update={"depends_on": deps}))
    return remapped


def _heuristic_options(
    question: str,
    router_result: RouterResult,
    *,
    min_options: int,
    max_options: int,
    max_steps: int,
) -> list[PlanOption]:
    """Conservative templates when LLM is unavailable or fails."""
    q = (question or "").strip()[:4000]
    rewrite = (router_result.rewrite_query or "").strip() or q
    skill = {
        "process_checklist": "process_checklist",
        "policy_compare": "policy_compare",
        "summary": "summary",
    }.get(router_result.task_type) or _infer_skill_hint(q) or "process_checklist"
    need_skill = router_result.need_skill or router_result.task_type in {
        "process_checklist",
        "policy_compare",
        "summary",
    }

    # Option A: single hybrid retrieve → skill? → answer (serial, cheap).
    steps_a: list[PlanStep] = [
        PlanStep(
            id="s1",
            title="统一混合检索相关制度",
            kind="retrieve",
            query=rewrite,
            status="pending",
        )
    ]
    if need_skill:
        steps_a.append(
            PlanStep(
                id="s2",
                title=f"执行业务规程（{skill}）",
                kind="skill",
                query=rewrite,
                skill_hint=skill,
                tool_hints=["skill.run"],
                depends_on=["s1"],
                status="pending",
            )
        )
    steps_a.append(
        PlanStep(
            id=f"s{len(steps_a) + 1}",
            title="基于证据生成回答",
            kind="answer",
            status="pending",
        )
    )
    steps_a = _ensure_answer_tail(steps_a, max_steps=max_steps)

    # Option B: split multi-query retrieve (parallel-friendly) → merge answer.
    half = max(8, len(q) // 2)
    q1 = rewrite[:half].strip() or rewrite
    q2 = rewrite[half:].strip() or f"{rewrite} 补充条款与例外"
    steps_b = [
        PlanStep(
            id="s1",
            title="检索主问题相关证据",
            kind="retrieve",
            query=q1[:4000],
            status="pending",
        ),
        PlanStep(
            id="s2",
            title="检索补充条款/例外证据",
            kind="retrieve",
            query=q2[:4000],
            status="pending",
        ),
    ]
    if need_skill:
        steps_b.append(
            PlanStep(
                id="s3",
                title=f"合并证据后执行规程（{skill}）",
                kind="skill",
                query=rewrite,
                skill_hint=skill,
                tool_hints=["skill.run"],
                depends_on=["s1", "s2"],
                status="pending",
            )
        )
    steps_b.append(
        PlanStep(
            id=f"s{len(steps_b) + 1}",
            title="汇总多路证据并回答",
            kind="answer",
            status="pending",
        )
    )
    steps_b = _ensure_answer_tail(steps_b, max_steps=max_steps)

    options: list[PlanOption] = [
        PlanOption(
            id="opt1",
            title="稳健串行：一次检索后作答",
            summary="单次 hybrid 检索，成本低、路径短，适合主题集中的问题。",
            tradeoffs=["覆盖面可能不足", "延迟通常更低"],
            steps=_reindex_steps(steps_a, "opt1"),
            recommended=True,
        ),
        PlanOption(
            id="opt2",
            title="并行分查：主问题 + 补充条款",
            summary="拆成两个 retrieve 同波并行，再合并证据（L2 可并行）。",
            tradeoffs=["检索次数更多", "覆盖更全，适合对比/多子问"],
            steps=_reindex_steps(steps_b, "opt2"),
            recommended=False,
        ),
    ]

    # Option C: skill-first / compare-oriented when task fits.
    if router_result.task_type in {"policy_compare", "process_checklist"} or "对比" in q:
        skill_c = "policy_compare" if "对比" in q or router_result.task_type == "policy_compare" else skill
        steps_c = [
            PlanStep(
                id="s1",
                title="分别检索对比维度相关制度",
                kind="retrieve",
                query=rewrite,
                status="pending",
            ),
            PlanStep(
                id="s2",
                title=f"优先结构化规程（{skill_c}）",
                kind="skill",
                query=rewrite,
                skill_hint=skill_c,
                tool_hints=["skill.run"],
                depends_on=["s1"],
                status="pending",
            ),
            PlanStep(
                id="s3",
                title="按规程结构生成可执行回答",
                kind="answer",
                status="pending",
            ),
        ]
        steps_c = _ensure_answer_tail(steps_c, max_steps=max_steps)
        options.append(
            PlanOption(
                id="opt3",
                title="规程优先：对比/清单结构化",
                summary="检索后优先跑业务 Skill，再组织最终回答。",
                tradeoffs=["依赖 skill 证据绑定", "结构更清晰"],
                steps=_reindex_steps(steps_c, "opt3"),
                recommended=False,
            )
        )

    # Clamp count.
    max_options = max(min_options, min(int(max_options or _DEFAULT_MAX_OPTIONS), 5))
    min_options = max(2, min(int(min_options or _DEFAULT_MIN_OPTIONS), max_options))
    options = options[:max_options]
    while len(options) < min_options:
        # Should not happen with templates above.
        break
    return _ensure_one_recommended(options)


def _ensure_one_recommended(options: list[PlanOption]) -> list[PlanOption]:
    if not options:
        return options
    if any(opt.recommended for opt in options):
        # Force single recommended.
        seen = False
        out: list[PlanOption] = []
        for opt in options:
            if opt.recommended and not seen:
                out.append(opt)
                seen = True
            else:
                out.append(opt.model_copy(update={"recommended": False}))
        return out
    return [options[0].model_copy(update={"recommended": True})] + [
        o.model_copy(update={"recommended": False}) for o in options[1:]
    ]


def _coerce_options_from_llm(
    data: dict[str, Any],
    *,
    question: str,
    router_result: RouterResult,
    min_options: int,
    max_options: int,
    max_steps: int,
) -> list[PlanOption]:
    raw_list = data.get("options") or data.get("plan_options") or []
    if not isinstance(raw_list, list):
        return []
    options: list[PlanOption] = []
    for idx, item in enumerate(raw_list, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        oid = str(item.get("id") or f"opt{idx}").strip() or f"opt{idx}"
        summary = str(item.get("summary") or item.get("description") or "").strip()[:400]
        tradeoffs_raw = item.get("tradeoffs") or []
        tradeoffs = (
            [str(t).strip() for t in tradeoffs_raw if str(t).strip()][:6]
            if isinstance(tradeoffs_raw, list)
            else []
        )
        steps = _normalize_option_steps(
            item.get("steps") or item.get("plan_steps"),
            question=question,
            router_result=router_result,
            max_steps=max_steps,
        )
        steps = _reindex_steps(steps, oid[:16])
        recommended = bool(item.get("recommended"))
        options.append(
            PlanOption(
                id=oid[:32],
                title=title[:120],
                summary=summary,
                tradeoffs=tradeoffs,
                steps=steps,
                recommended=recommended,
            )
        )
        if len(options) >= max_options:
            break
    if len(options) < min_options:
        return []
    return _ensure_one_recommended(options[:max_options])


async def generate_plan_options(
    question: str,
    router_result: RouterResult,
    *,
    llm_service: LLMService | None = None,
    min_options: int = _DEFAULT_MIN_OPTIONS,
    max_options: int = _DEFAULT_MAX_OPTIONS,
    max_steps: int = _DEFAULT_MAX_STEPS,
) -> list[PlanOption]:
    """Return 2–3 candidate plans for user selection."""
    min_options = max(2, min(int(min_options or _DEFAULT_MIN_OPTIONS), 5))
    max_options = max(min_options, min(int(max_options or _DEFAULT_MAX_OPTIONS), 5))
    max_steps = max(1, min(int(max_steps or _DEFAULT_MAX_STEPS), 10))

    if llm_service is not None:
        system = (
            "你为企业制度问答生成「候选执行计划」供用户选择。"
            "只输出 JSON："
            '{"options":[{"id":"opt1","title":str,"summary":str,"tradeoffs":[str],'
            '"recommended":bool,'
            '"steps":[{"id":str,"title":str,"kind":"retrieve|skill|answer|tool|verify",'
            '"query":str|null,"skill_hint":str|null,"tool_hints":[str],"depends_on":[str]}]}]}。'
            f"生成 {min_options}-{max_options} 条差异化路径（检索策略/顺序/是否 skill 不同），"
            "每条 2-5 步，最后一步 kind=answer。"
            "恰好一条 recommended=true。"
            "这不是学术 Tree-of-Thoughts 搜索，不要写推理散文。"
            "不要解释。"
        )
        user = (
            f"问题：{question}\n"
            f"task_type={router_result.task_type}, domain={router_result.domain}, "
            f"need_skill={router_result.need_skill}, "
            f"rewrite_query={router_result.rewrite_query or ''}"
        )
        try:
            raw = await llm_service.complete(system, user)
            parsed = _parse_json_object(raw)
            if parsed is not None:
                options = _coerce_options_from_llm(
                    parsed,
                    question=question,
                    router_result=router_result,
                    min_options=min_options,
                    max_options=max_options,
                    max_steps=max_steps,
                )
                if len(options) >= min_options:
                    return options
        except Exception:
            pass

    return _heuristic_options(
        question,
        router_result,
        min_options=min_options,
        max_options=max_options,
        max_steps=max_steps,
    )


def pick_recommended_option(options: list[PlanOption]) -> PlanOption | None:
    if not options:
        return None
    for opt in options:
        if opt.recommended:
            return opt
    return options[0]


def find_option(options: list[PlanOption], option_id: str | None) -> PlanOption | None:
    if not option_id:
        return None
    for opt in options:
        if opt.id == option_id:
            return opt
    return None
