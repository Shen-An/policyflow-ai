"""Canonical skill names and a resolver for planner-provided hints.

The Router / ToT branch LLM writes `plan_steps[].skill_hint` freely, and in
practice it emits descriptive Chinese phrases such as「流程清单抽取」or
「报销流程解析技能：识别制度中的报销条件、审批链、材料清单」. Those names are
not registered skills, so running them raises `SKILL_NOT_FOUND` and the plan
step turns into a hard red failure even though the intent was a plain
checklist.

Resolve hints against the implemented set here, and return ``None`` when there
is no honest match so callers can skip the step instead of inventing a skill.
"""

from __future__ import annotations

# Skills with a real handler + input model in the registry.
IMPLEMENTED_SKILLS: tuple[str, ...] = ("process_checklist", "policy_compare", "summary")

# Names the planner (or a user) may write instead of the canonical one.
_ALIASES: dict[str, str] = {
    "checklist": "process_checklist",
    "process": "process_checklist",
    "process_list": "process_checklist",
    "processchecklist": "process_checklist",
    "flow_checklist": "process_checklist",
    "compare": "policy_compare",
    "comparison": "policy_compare",
    "policy_diff": "policy_compare",
    "policycompare": "policy_compare",
    "summarize": "summary",
    "summarization": "summary",
    "summarise": "summary",
    "abstract": "summary",
}

# Keyword scan for free-form hints. Order matters: a phrase like
#「对比两地报销流程清单」is a comparison first, a checklist second.
_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("对比", "比较", "区别", "差异", "compare", "diff"), "policy_compare"),
    (("摘要", "总结", "概括", "summary", "summarize"), "summary"),
    (
        (
            "清单",
            "流程",
            "步骤",
            "材料",
            "审批",
            "指引",
            "checklist",
            "process",
            "step",
            "procedure",
        ),
        "process_checklist",
    ),
)


def resolve_skill_name(raw: str | None) -> str | None:
    """Map an arbitrary skill hint to an implemented skill, or ``None``.

    ``None`` means "no honest match" — callers should skip rather than guess.
    """

    text = (raw or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in IMPLEMENTED_SKILLS:
        return lowered
    alias = _ALIASES.get(lowered) or _ALIASES.get(lowered.replace("-", "_").replace(" ", "_"))
    if alias:
        return alias
    for terms, name in _KEYWORD_RULES:
        if any(term in lowered for term in terms):
            return name
    return None
