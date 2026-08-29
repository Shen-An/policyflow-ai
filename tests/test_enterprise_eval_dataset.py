"""Contract tests for the built-in enterprise evaluation suite."""

from backend.app.evals.negatives import NEGATIVE_KINDS, negative_kind
from backend.app.services.enterprise_eval_dataset import (
    ENTERPRISE_EVAL_KB_CODE,
    ENTERPRISE_EVAL_SUITE,
    POLICY_CASES,
    POLICY_DOCUMENTS,
    POLICY_FACTS,
    POLICY_NEGATIVES,
)


def test_enterprise_suite_is_large_enough_for_non_toy_retrieval_eval() -> None:
    document_ids = {item.external_id for item in POLICY_DOCUMENTS}
    case_keys = [item.key for item in POLICY_CASES]

    assert ENTERPRISE_EVAL_KB_CODE == "enterprise_eval_test"
    assert ENTERPRISE_EVAL_SUITE == "enterprise_policy_v1"
    assert len(POLICY_DOCUMENTS) == 12
    assert len(POLICY_CASES) == 200
    assert sum(len(facts) for facts in POLICY_FACTS.values()) == 88
    assert set(POLICY_FACTS) == document_ids
    assert len(document_ids) == len(POLICY_DOCUMENTS)
    assert len(case_keys) == len(set(case_keys))
    assert len({item.question for item in POLICY_CASES}) == 200
    assert all(
        set(case.relevant_documents).issubset(document_ids) for case in POLICY_CASES
    )
    assert sum(len(case.relevant_documents) > 1 for case in POLICY_CASES) >= 5
    assert sum(case.difficulty == "boundary" for case in POLICY_CASES) >= 4


def test_negative_queries_cover_both_kinds_and_never_collide_with_positives() -> None:
    positives = {case.question for case in POLICY_CASES}
    keys = [item.key for item in POLICY_NEGATIVES]

    assert len(POLICY_NEGATIVES) == 40
    assert len(keys) == len(set(keys))
    assert {item.kind for item in POLICY_NEGATIVES} == set(NEGATIVE_KINDS)
    assert sum(item.kind == "off_topic" for item in POLICY_NEGATIVES) == 20
    assert sum(item.kind == "near_miss" for item in POLICY_NEGATIVES) == 20
    assert not positives & {item.query for item in POLICY_NEGATIVES}


def test_negative_marker_round_trips_through_relevance_judgement() -> None:
    judgement = {
        "source": "enterprise_policy",
        "suite": ENTERPRISE_EVAL_SUITE,
        "negative": True,
        "negative_kind": "near_miss",
        "gold_doc_count": 0,
    }

    assert negative_kind(judgement) == "near_miss"
    # A gold-less positive (deleted documents) must stay distinguishable.
    assert negative_kind({"source": "enterprise_policy", "gold_doc_count": 0}) is None
    assert negative_kind(None) is None
