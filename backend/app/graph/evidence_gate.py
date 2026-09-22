"""Deterministic evidence gate shared by every entrypoint (T040, T048).

This is the single place that decides whether retrieved material is strong
enough to ground a policy answer. It is deterministic and side-effect free so
that chat, stream, eval and the file workflow reach the *same* verdict for the
same evidence — that parity is the whole point of the unified graph.

Fail-closed rules (see ``docs/08`` and ``contracts/internal-contracts.md``):

- Retrieval unavailable → insufficient. We never fabricate a conclusion when we
  could not look.
- Cross-tenant material never counts. A row belonging to another tenant is not
  this tenant's policy, no matter how relevant it looks.
- Memory is non-authoritative. A recalled preference or past turn can shape
  phrasing but can never *be* the policy citation.
- Non-retrievable / low-relevance material is rejected. Off-topic hits are not
  institutional grounds.

The scoring here is honest local lexical relevance, not a cross-encoder; nothing
in this module should be described as a cross-encoder rerank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "RELEVANCE_THRESHOLD",
    "EvidenceCandidate",
    "EvidenceGateDecision",
    "EvidenceGateInput",
    "EvidenceGateResult",
    "evaluate_evidence_gate",
]

# Minimum local relevance for a candidate to be treated as grounding. Kept as a
# named constant so calibration lives in one auditable place.
RELEVANCE_THRESHOLD = 0.5

# Only knowledge-base material is authoritative policy evidence. Memory and any
# other source type are explicitly excluded from the policy gate.
_POLICY_SOURCE_TYPES = frozenset({"knowledge_base"})


class EvidenceGateDecision(str, Enum):
    SUPPORTED = "supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class EvidenceCandidate:
    """One retrieved candidate considered by the gate."""

    evidence_id: str
    tenant_id: str
    knowledge_base_id: str
    document_version: str
    content: str
    relevance_score: float
    retrievable: bool = True
    source_type: str = "knowledge_base"


@dataclass(frozen=True)
class EvidenceGateInput:
    tenant_id: str
    query: str
    candidates: list[EvidenceCandidate] = field(default_factory=list)
    retrieval_available: bool = True
    entrypoint: str = "chat"


@dataclass(frozen=True)
class EvidenceGateResult:
    decision: EvidenceGateDecision
    accepted_evidence_ids: tuple[str, ...]
    reason: str


def _is_acceptable(candidate: EvidenceCandidate, *, tenant_id: str) -> bool:
    if candidate.tenant_id != tenant_id:
        return False
    if not candidate.retrievable:
        return False
    if candidate.source_type not in _POLICY_SOURCE_TYPES:
        return False
    return candidate.relevance_score >= RELEVANCE_THRESHOLD


def evaluate_evidence_gate(gate_input: EvidenceGateInput) -> EvidenceGateResult:
    """Return the same verdict for the same evidence, regardless of entrypoint."""
    if not gate_input.retrieval_available:
        return EvidenceGateResult(
            decision=EvidenceGateDecision.INSUFFICIENT_EVIDENCE,
            accepted_evidence_ids=(),
            reason="retrieval_unavailable",
        )

    accepted = tuple(
        candidate.evidence_id
        for candidate in gate_input.candidates
        if _is_acceptable(candidate, tenant_id=gate_input.tenant_id)
    )
    if accepted:
        return EvidenceGateResult(
            decision=EvidenceGateDecision.SUPPORTED,
            accepted_evidence_ids=accepted,
            reason="supported",
        )
    return EvidenceGateResult(
        decision=EvidenceGateDecision.INSUFFICIENT_EVIDENCE,
        accepted_evidence_ids=(),
        reason="insufficient_evidence",
    )
