"""Deterministic retrieval quality gate."""

from __future__ import annotations

from typing import Any, Literal

from backend.app.agents.grounding import question_evidence_support
from backend.app.schemas.retrieval import Evidence

QualityDecision = Literal["accept", "retry", "refuse"]


def assess_retrieval_quality(
    question: str,
    evidence: list[Evidence],
    *,
    rewritten_query: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    support = question_evidence_support(question, evidence)
    synthetic_ratio = (
        sum(bool((item.metadata or {}).get("score_is_synthetic")) for item in evidence)
        / len(evidence)
        if evidence
        else 0.0
    )
    drift = bool(rewritten_query and rewritten_query.strip() and not support["supported"])
    reasons: list[str] = []
    if not evidence:
        reasons.append("NO_RELIABLE_EVIDENCE")
    if evidence and not support["supported"]:
        reasons.append("RETRIEVAL_LOW_QUALITY")
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
        "overlap_ratio": support.get("overlap_ratio", 0.0),
        "evidence_count": len(evidence),
        "synthetic_score_ratio": round(synthetic_ratio, 4),
        "attempt": attempt,
    }
