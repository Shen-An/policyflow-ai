"""Contract tests for the built-in enterprise evaluation suite."""

from backend.app.services.enterprise_eval_dataset import (
    ENTERPRISE_EVAL_KB_CODE,
    ENTERPRISE_EVAL_SUITE,
    POLICY_CASES,
    POLICY_DOCUMENTS,
    POLICY_FACTS,
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
