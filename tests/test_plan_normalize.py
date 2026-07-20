"""Unit tests for progressive multi-step plan normalization (L1)."""

from __future__ import annotations

from backend.app.agents.plan_normalize import (
    merge_plan_tool_hints,
    normalize_plan,
    parse_user_numbered_steps,
    plan_needs_skill,
    primary_retrieve_query,
    should_be_multi_step,
)
from backend.app.schemas.chat import PlanStep, RouterResult


def _router(**kwargs) -> RouterResult:
    base = {
        "domain": "hr",
        "task_type": "knowledge_qa",
        "risk_level": "low",
        "need_skill": False,
        "tool_hints": [],
        "rewrite_query": None,
        "complexity": "simple",
        "plan_steps": [],
        "plan_source": "none",
    }
    base.update(kwargs)
    return RouterResult(**base)


def test_parse_user_numbered_steps() -> None:
    question = (
        "请分三步：\n"
        "1. 查差旅住宿标准\n"
        "2. 列申请清单\n"
        "3. 给填写模板\n"
    )
    steps = parse_user_numbered_steps(question)
    assert len(steps) >= 3
    assert steps[0].kind in {"retrieve", "skill", "tool", "answer"}
    assert "差旅" in steps[0].title or "住宿" in steps[0].title


def test_simple_question_stays_simple() -> None:
    router = _router()
    result = normalize_plan("年假有多少天？", router)
    assert result.complexity == "simple"
    assert result.plan_steps == []
    assert result.plan_source == "none"


def test_user_steps_take_priority() -> None:
    router = _router(complexity="simple")
    question = "1. 查制度\n2. 列清单\n3. 回答"
    result = normalize_plan(question, router)
    assert result.complexity == "multi_step"
    assert result.plan_source == "user"
    assert len(result.plan_steps) >= 2
    assert any(step.kind == "answer" for step in result.plan_steps)


def test_heuristic_multi_step_builds_default_plan() -> None:
    router = _router(
        task_type="policy_compare",
        need_skill=True,
        tool_hints=["skill.run"],
    )
    question = "对比一线与二线差旅住宿标准，并给出报销申请清单"
    assert should_be_multi_step(question, router) is True
    result = normalize_plan(question, router)
    assert result.complexity == "multi_step"
    assert len(result.plan_steps) >= 2
    assert any(step.kind == "retrieve" for step in result.plan_steps)
    assert any(step.kind == "answer" for step in result.plan_steps)


def test_max_steps_truncation() -> None:
    router = _router(
        complexity="multi_step",
        plan_steps=[
            PlanStep(id=f"s{i}", title=f"步骤{i}", kind="retrieve")
            for i in range(1, 10)
        ],
    )
    result = normalize_plan("请分步处理以下复杂任务并且还要汇总", router, max_steps=3)
    assert result.complexity == "multi_step"
    assert len(result.plan_steps) <= 3
    assert any(step.kind == "answer" for step in result.plan_steps)


def test_planning_disabled() -> None:
    router = _router(
        complexity="multi_step",
        plan_steps=[PlanStep(id="s1", title="查", kind="retrieve")],
    )
    result = normalize_plan("1. a\n2. b", router, enabled=False)
    assert result.complexity == "simple"
    assert result.plan_steps == []


def test_primary_retrieve_query_and_tool_hints() -> None:
    steps = [
        PlanStep(id="s1", title="检索", kind="retrieve", query="差旅标准"),
        PlanStep(
            id="s2",
            title="清单",
            kind="skill",
            skill_hint="process_checklist",
            tool_hints=["skill.run"],
        ),
        PlanStep(id="s3", title="回答", kind="answer"),
    ]
    assert primary_retrieve_query(steps, "fallback") == "差旅标准"
    hints = merge_plan_tool_hints(steps, ["kb.search"])
    assert "kb.search" in hints
    assert "skill.run" in hints
    assert plan_needs_skill(steps, False) is True
