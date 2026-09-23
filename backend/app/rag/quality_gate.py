"""Deterministic retrieval quality gate — moved to the canonical evidence gate.

The implementation now lives in :mod:`backend.app.retrieval.evidence_gate` (T048,
the spec-mandated home). This module is a thin backward-compatibility re-export so
existing importers and tests keep working against the single source of truth. Prefer
importing from ``backend.app.retrieval.evidence_gate`` in new code.
"""

from __future__ import annotations

from backend.app.retrieval.evidence_gate import (
    QualityDecision,
    assess_retrieval_quality,
    off_topic_reason,
    resolve_gate_thresholds,
)

__all__ = [
    "QualityDecision",
    "resolve_gate_thresholds",
    "assess_retrieval_quality",
    "off_topic_reason",
]
