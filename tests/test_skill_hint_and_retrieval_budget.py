"""Skill hint canonicalization + bounded degradation instead of red failures.

Regression cover for two recurring chat failures:
1. Router LLM wrote free-text skill names（如「流程清单抽取」）→ SKILL_NOT_FOUND.
2. The retrieval budget was so tight that the answer loop's supplementary
   kb.search always died with TURN_BUDGET_EXHAUSTED and showed as a red
   "工具 kb.search failed".
"""

from __future__ import annotations

import pytest

from backend.app.agents.plan_normalize import _coerce_step
from backend.app.agents.router_agent import _coerce_plan_steps
from backend.app.agents.skill_agent import SkillAgent
from backend.app.agents.turn_budget import TurnBudget, current_turn_budget
from backend.app.core.config import Settings
from backend.app.skills.catalog import IMPLEMENTED_SKILLS, resolve_skill_name
from backend.app.tools.chat_tools import ChatToolExecutor

# Observed verbatim in ai_query_logs.diagnostics on 2026-08-28.
OBSERVED_HINTS = (
    "流程清单抽取",
    "报销流程解析技能：识别制度中的报销条件、审批链、材料清单",
)


@pytest.mark.parametrize("raw", OBSERVED_HINTS)
def test_observed_free_text_hints_resolve_to_process_checklist(raw: str) -> None:
    assert resolve_skill_name(raw) == "process_checklist"


def test_resolver_keeps_implemented_names_and_rejects_unrelated_text() -> None:
    for name in IMPLEMENTED_SKILLS:
        assert resolve_skill_name(name) == name
    assert resolve_skill_name("checklist") == "process_checklist"
    assert resolve_skill_name("对比两份制度的区别") == "policy_compare"
    assert resolve_skill_name("给制度做摘要") == "summary"
    assert resolve_skill_name("发送邮件通知同事") is None
    assert resolve_skill_name(None) is None
    assert resolve_skill_name("   ") is None


def test_plan_normalize_canonicalizes_router_skill_hint() -> None:
    step = _coerce_step(
        {
            "id": "2",
            "title": "生成申请材料草稿",
            "kind": "skill",
            "skill_hint": "流程清单抽取",
        },
        2,
    )

    assert step is not None
    assert step.skill_hint == "process_checklist"


def test_router_plan_steps_drop_unresolvable_skill_hint() -> None:
    steps = _coerce_plan_steps(
        [
            {"id": "1", "title": "检索报销制度", "kind": "retrieve"},
            {
                "id": "2",
                "title": "生成申请材料草稿",
                "kind": "skill",
                "skill_hint": "报销流程解析技能：识别制度中的报销条件、审批链、材料清单",
            },
            {"id": "3", "title": "整理最终回答", "kind": "answer", "skill_hint": "乱写的技能"},
        ]
    )

    assert [s.skill_hint for s in steps] == [None, "process_checklist", None]


@pytest.mark.asyncio
async def test_execute_one_skips_unregistered_skill_instead_of_failing() -> None:
    agent = SkillAgent(skill_registry=None)

    result = await agent.execute_one(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        "发送邮件通知同事",
        "帮我通知一下",
        [],
        step_id="2",
    )

    assert result["status"] == "skipped"
    assert "未注册的 Skill" in str(result["error"])


def _executor(**kwargs) -> ChatToolExecutor:
    return ChatToolExecutor(
        session=None,  # type: ignore[arg-type]
        user=None,  # type: ignore[arg-type]
        tool_registry=None,  # type: ignore[arg-type]
        allowed_tools={"kb.search", "skill.run"},
        knowledge_base_ids=["kb-1"],
        rag_service=object(),  # type: ignore[arg-type]
        **kwargs,
    )


@pytest.mark.asyncio
async def test_kb_search_degrades_when_retrieval_budget_is_spent() -> None:
    budget = TurnBudget(max_retrieval_attempts=1)
    budget.reserve("retrieval")
    token = current_turn_budget.set(budget)
    try:
        output = await _executor()._kb_search({"query": "差旅费用报销 审批要求", "top_k": 8})
    finally:
        current_turn_budget.reset(token)

    assert output["degraded"] is True
    assert output["warning"] == "retrieval_budget_exhausted"
    assert output["evidence"] == []
    # The bound is still reported honestly; it just is not a tool failure.
    assert budget.snapshot()["retrieval_attempts"] == 1


@pytest.mark.asyncio
async def test_skill_run_reports_unknown_skill_without_raising() -> None:
    executor = _executor()
    executor.skill_registry = object()  # type: ignore[assignment]

    output = await executor._skill_run({"name": "发送邮件通知同事"})

    assert output["degraded"] is True
    assert output["warning"] == "skill_not_registered"
    assert "process_checklist" in output["message"]


def test_retrieval_budget_leaves_room_for_a_supplementary_search() -> None:
    settings = Settings(_env_file=None)

    # Pipeline retrieval takes 1, the quality-gate retry may take a 2nd;
    # anything less than 3 means the answer loop can never search at all.
    assert settings.CHAT_TURN_MAX_RETRIEVAL_ATTEMPTS >= 3
