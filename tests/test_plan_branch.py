"""Unit tests for ToT-style plan option generation (not academic ToT search)."""

from __future__ import annotations

import asyncio

from backend.app.agents.plan_branch import (
    find_option,
    generate_plan_options,
    pick_recommended_option,
)
from backend.app.schemas.chat import PlanOption, PlanStep, RouterResult


def _router(**kwargs) -> RouterResult:
    base = {
        "domain": "hr",
        "task_type": "policy_compare",
        "risk_level": "low",
        "need_skill": True,
        "tool_hints": ["skill.run"],
        "rewrite_query": None,
        "complexity": "multi_step",
        "difficulty": "branched",
        "reasoning_mode": "tot_select",
        "plan_steps": [
            PlanStep(id="s1", title="检索制度", kind="retrieve", query="差旅标准"),
            PlanStep(id="s2", title="生成清单", kind="skill", skill_hint="process_checklist"),
            PlanStep(id="s3", title="回答", kind="answer"),
        ],
        "plan_source": "router",
    }
    base.update(kwargs)
    return RouterResult(**base)


def test_generate_plan_options_heuristic() -> None:
    options = asyncio.run(
        generate_plan_options(
            "对比一线与二线差旅住宿标准，并给出报销申请清单",
            _router(),
            min_options=2,
            max_options=3,
        )
    )
    assert 2 <= len(options) <= 3
    assert all(isinstance(opt, PlanOption) for opt in options)
    assert all(opt.steps for opt in options)
    assert any(opt.recommended for opt in options)
    assert sum(1 for opt in options if opt.recommended) == 1
    ids = [opt.id for opt in options]
    assert len(ids) == len(set(ids))


def test_pick_and_find_option() -> None:
    options = [
        PlanOption(id="opt1", title="A", steps=[PlanStep(id="s1", title="x")], recommended=False),
        PlanOption(id="opt2", title="B", steps=[PlanStep(id="s1", title="y")], recommended=True),
    ]
    assert pick_recommended_option(options).id == "opt2"
    assert find_option(options, "opt1") is not None
    assert find_option(options, "missing") is None
