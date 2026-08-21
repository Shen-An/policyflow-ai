"""Deterministic retrieval quality gate."""

from __future__ import annotations

from typing import Any, Literal

from backend.app.agents.grounding import (
    DEFAULT_MIN_OVERLAP_RATIO,
    question_evidence_relevance,
)
from backend.app.core.config import Settings, get_settings
from backend.app.schemas.retrieval import Evidence

QualityDecision = Literal["accept", "retry", "refuse"]

_UNSET = object()


def resolve_gate_thresholds(
    settings: Settings | None = None,
) -> tuple[float | None, float]:
    """(cross-encoder score threshold or None if disabled, lexical overlap ratio)."""
    app_settings = settings or get_settings()
    score_threshold: float | None = None
    if bool(getattr(app_settings, "RETRIEVAL_GATE_CROSS_ENCODER_ENABLED", True)):
        score_threshold = float(
            getattr(app_settings, "RETRIEVAL_GATE_MIN_CROSS_ENCODER_SCORE", -8.0)
        )
    overlap_ratio = float(
        getattr(app_settings, "RETRIEVAL_GATE_MIN_OVERLAP_RATIO", DEFAULT_MIN_OVERLAP_RATIO)
    )
    return score_threshold, overlap_ratio


def assess_retrieval_quality(
    question: str,
    evidence: list[Evidence],
    *,
    rewritten_query: str | None = None,
    attempt: int = 1,
    settings: Settings | None = None,
    min_cross_encoder_score: float | None | Any = _UNSET,
    min_overlap_ratio: float | Any = _UNSET,
) -> dict[str, Any]:
    """Judge retrieved evidence before it is allowed to ground an answer.

    Off-topic detection prefers the cross-encoder relevance score and falls back
    to lexical bigram coverage when the retrieval path produced no such score.
    Thresholds come from settings unless explicitly passed (tests, calibration).
    """
    default_score_threshold, default_overlap_ratio = resolve_gate_thresholds(settings)
    score_threshold = (
        default_score_threshold if min_cross_encoder_score is _UNSET else min_cross_encoder_score
    )
    overlap_ratio = (
        default_overlap_ratio if min_overlap_ratio is _UNSET else float(min_overlap_ratio)
    )
    support = question_evidence_relevance(
        question,
        evidence,
        min_cross_encoder_score=score_threshold,
        min_overlap_ratio=overlap_ratio,
    )
    synthetic_ratio = (
        sum(bool((item.metadata or {}).get("score_is_synthetic")) for item in evidence)
        / len(evidence)
        if evidence
        else 0.0
    )
    drift = bool(rewritten_query and rewritten_query.strip() and not support["supported"])
    off_topic = bool(evidence) and not support["supported"]
    reasons: list[str] = []
    if not evidence:
        reasons.append("NO_RELIABLE_EVIDENCE")
    if off_topic:
        reasons.append(
            "RETRIEVAL_SCORE_BELOW_THRESHOLD"
            if support["gate"] == "cross_encoder_score"
            else "RETRIEVAL_LOW_QUALITY"
        )
    if drift:
        reasons.append("QUERY_REWRITE_DRIFT")
    # Synthetic rank decay is metadata, not a similarity threshold. It is
    # reported for diagnostics but does not reject otherwise supported evidence.
    if not reasons:
        decision: QualityDecision = "accept"
    elif attempt < 2:
        decision = "retry"
    else:
        decision = "refuse"
    return {
        "decision": decision,
        "reason_codes": reasons,
        "off_topic": off_topic,
        "gate": support["gate"],
        "top_score": support["top_score"],
        "score_threshold": support["score_threshold"],
        "lexical_supported": support["lexical_supported"],
        "min_overlap_ratio": support["min_overlap_ratio"],
        "overlap_ratio": support.get("overlap_ratio", 0.0),
        "evidence_count": len(evidence),
        "synthetic_score_ratio": round(synthetic_ratio, 4),
        "attempt": attempt,
    }


def off_topic_reason(quality: dict[str, Any]) -> str:
    """Human-readable why-dropped text shared by chat stages and diagnostics."""
    if quality.get("gate") == "cross_encoder_score":
        top_score = quality.get("top_score")
        threshold = quality.get("score_threshold")
        return (
            f"重排相关度过低（top1={top_score:.2f} < 阈值 {threshold:.2f}）"
            if isinstance(top_score, int | float) and isinstance(threshold, int | float)
            else "重排相关度过低"
        )
    return f"与原问题词面相关度过低（overlap={quality.get('overlap_ratio')}）"
