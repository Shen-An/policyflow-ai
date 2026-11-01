"""The shadow diff must separate real divergence from deliberate degradation.

``_explain_degradation`` is the only thing standing between "the graph matches the
old path" and "the shadow simply did not execute skills". These cases pin both
directions: what may be explained away, and what must never be.
"""

from __future__ import annotations

from backend.app.graph.shadow import SKILL_CONFIDENCE_BONUS, _explain_degradation


def _field(left: object, right: object) -> dict[str, object]:
    return {"authoritative": left, "shadow": right, "match": left == right}


def _answer(text: str, confidence: float, tool_trace: int = 0) -> dict[str, object]:
    return {
        "text": {"len": len(text), "sha256": text},
        "confidence": confidence,
        "tool_trace": tool_trace,
    }


def test_skill_fields_are_explained_only_when_side_effects_are_disabled() -> None:
    field_diff = {"skills": _field({"results": [{"status": "success"}]}, {"results": []})}

    explained = _explain_degradation(field_diff, side_effects_disabled=True)
    assert "skills" in explained

    # With side effects enabled the shadow *should* have run the skill, so the very
    # same mismatch is a genuine divergence and must stay unexplained.
    assert _explain_degradation(field_diff, side_effects_disabled=False) == {}


def test_answer_mismatch_is_only_the_skill_confidence_bonus() -> None:
    field_diff = {
        "answer": _field(
            _answer("按照制度，差旅需经理审批。", 0.67),
            _answer("按照制度，差旅需经理审批。", 0.67 - SKILL_CONFIDENCE_BONUS),
        )
    }

    explained = _explain_degradation(field_diff, side_effects_disabled=True)
    assert "answer" in explained


def test_answer_text_divergence_is_never_explained_away() -> None:
    field_diff = {
        "answer": _field(
            _answer("答案是 A", 0.67),
            _answer("答案是 B", 0.67 - SKILL_CONFIDENCE_BONUS),
        )
    }

    assert _explain_degradation(field_diff, side_effects_disabled=True) == {}


def test_confidence_gap_that_is_not_the_skill_bonus_stays_unexplained() -> None:
    field_diff = {
        "answer": _field(
            _answer("按照制度，差旅需经理审批。", 0.80),
            _answer("按照制度，差旅需经理审批。", 0.50),
        )
    }

    assert _explain_degradation(field_diff, side_effects_disabled=True) == {}


def test_unrelated_fields_are_never_explained() -> None:
    field_diff = {
        "router": _field({"task_type": "policy_qa"}, {"task_type": "process_checklist"}),
        "compliance": _field({"verdict": "pass"}, {"verdict": "block"}),
        "errors": _field([], ["REFLECTION_CRITIQUE_PARSE_ERROR"]),
    }

    assert _explain_degradation(field_diff, side_effects_disabled=True) == {}
