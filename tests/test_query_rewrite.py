"""Tests for multi-turn retrieval query rewriting."""

from backend.app.services.query_rewrite import (
    expand_retrieval_query,
    extract_topic_terms,
    is_followup_question,
)


def test_followup_template_inherits_reimbursement_topic() -> None:
    history = [
        {"role": "user", "content": "怎么报销"},
        {"role": "assistant", "content": "报销需在三十日内提交。"},
    ]
    expanded = expand_retrieval_query("给我模板", history=history)
    assert "模板" in expanded
    assert "报销" in expanded
    assert expanded != "给我模板"


def test_followup_too_brief_inherits_prior_question() -> None:
    history = [{"role": "user", "content": "差旅住宿标准是多少？"}]
    expanded = expand_retrieval_query("那报销呢", history=history)
    assert "报销" in expanded
    assert "差旅" in expanded or "住宿" in expanded


def test_standalone_domain_question_not_overwritten() -> None:
    history = [{"role": "user", "content": "怎么报销"}]
    expanded = expand_retrieval_query("合同审批流程有哪些步骤", history=history)
    assert expanded == "合同审批流程有哪些步骤"


def test_is_followup_and_topic_helpers() -> None:
    assert is_followup_question("给我模板")
    assert is_followup_question("详细一点")
    assert not is_followup_question("财务报销需要哪些附件材料清单")
    assert "报销" in extract_topic_terms("怎么报销费用")
