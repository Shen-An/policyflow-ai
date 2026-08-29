"""Negative eval items: gold-less queries scored by the off-topic gate, not Hit@K."""

from backend.app.evals.eval_runner import _negative_aggregate
from backend.app.evals.negatives import is_negative_judgement, negative_kind
from backend.app.evals.retrieval_metrics import (
    calculate_negative_gate_metrics,
    calculate_retrieval_metrics,
)
from backend.app.db.models import RetrievalEvalItem
from backend.app.schemas.retrieval import Evidence
from backend.app.services.eval_service import _is_stale_retrieval_item


def _evidence(rank: int = 1, document_id: str = "doc-1") -> Evidence:
    return Evidence(
        knowledge_base_id="kb",
        knowledge_base_name="测试库",
        document_id=document_id,
        snippet="差旅住宿标准为 500 元",
        score=0.4,
        retriever_type="bm25",
        rank=rank,
    )


def _negative_item(kind: str = "near_miss") -> RetrievalEvalItem:
    return RetrievalEvalItem(
        query="婚假可以休几天",
        knowledge_base_ids=["kb"],
        relevant_document_ids=[],
        relevance_judgement={
            "source": "enterprise_policy",
            "negative": True,
            "negative_kind": kind,
            "gold_doc_count": 0,
        },
    )


def test_blocked_negative_scores_one_and_records_both_gate_signals() -> None:
    metrics = calculate_negative_gate_metrics(
        [_evidence()],
        {
            "off_topic": True,
            "gate": "cross_encoder_score",
            "lexical_supported": True,
            "overlap_ratio": 0.21,
            "top_score": -12.5,
            "score_threshold": -8.0,
            "reason_codes": ["RETRIEVAL_SCORE_BELOW_THRESHOLD"],
        },
        negative_kind="near_miss",
    )

    assert metrics["status"] == "completed"
    assert metrics["kind"] == "negative"
    assert metrics["gate_blocked"] == 1.0
    # The old lexical-only gate would have let this through — that is the delta to report.
    assert metrics["lexical_blocked"] == 0.0
    assert metrics["gate_mode"] == "cross_encoder_score"
    assert metrics["top_score"] == -12.5
    assert metrics["score_threshold"] == -8.0
    assert "hit_at_1" not in metrics
    assert "mrr" not in metrics


def test_unblocked_negative_scores_zero() -> None:
    metrics = calculate_negative_gate_metrics(
        [_evidence()],
        {"off_topic": False, "gate": "lexical_overlap", "lexical_supported": True},
        negative_kind="off_topic",
    )

    assert metrics["gate_blocked"] == 0.0
    assert metrics["retrieved_count"] == 1


def test_empty_retrieval_counts_as_blocked() -> None:
    metrics = calculate_negative_gate_metrics([], {}, negative_kind="off_topic")

    assert metrics["gate_blocked"] == 1.0
    assert metrics["gate_mode"] == "unknown"


def test_negatives_would_be_skipped_by_the_positive_metric_path() -> None:
    """Why negatives need their own path: no gold means no Hit@K to compute."""
    assert calculate_retrieval_metrics([_evidence()], [], [], [1, 5]) == {
        "status": "skipped",
        "reason": "empty_ground_truth",
    }


def test_negative_aggregate_splits_by_kind_and_counts_gate_modes() -> None:
    aggregate = _negative_aggregate(
        [
            calculate_negative_gate_metrics(
                [_evidence()],
                {"off_topic": True, "gate": "cross_encoder_score", "lexical_supported": True},
                negative_kind="near_miss",
            ),
            calculate_negative_gate_metrics(
                [_evidence()],
                {"off_topic": False, "gate": "cross_encoder_score", "lexical_supported": True},
                negative_kind="near_miss",
            ),
            calculate_negative_gate_metrics(
                [_evidence()],
                {"off_topic": True, "gate": "lexical_overlap", "lexical_supported": False},
                negative_kind="off_topic",
            ),
        ]
    )

    assert aggregate["count"] == 3
    assert aggregate["gate_blocked"] == 2 / 3
    assert aggregate["by_kind"]["near_miss"]["count"] == 2
    assert aggregate["by_kind"]["near_miss"]["gate_blocked"] == 0.5
    assert aggregate["by_kind"]["off_topic"]["gate_blocked"] == 1.0
    assert aggregate["gate_modes"] == {"cross_encoder_score": 2, "lexical_overlap": 1}


def test_negative_items_survive_the_stale_gold_hygiene_pass() -> None:
    """cleanup_eval_dataset deletes gold-less items; negatives must be exempt."""
    positive_without_gold = RetrievalEvalItem(
        query="差旅住宿标准",
        knowledge_base_ids=["kb"],
        relevant_document_ids=[],
        relevance_judgement={"source": "enterprise_policy"},
    )

    assert _is_stale_retrieval_item(_negative_item(), {}) is False
    assert _is_stale_retrieval_item(positive_without_gold, {}) is True


def test_negative_marker_helpers_agree() -> None:
    item = _negative_item(kind="off_topic")

    assert is_negative_judgement(item.relevance_judgement) is True
    assert negative_kind(item.relevance_judgement) == "off_topic"
    assert negative_kind({"negative": True}) == "unknown"
    assert is_negative_judgement({"gold_doc_count": 0}) is False
