"""Failing contracts for deterministic evidence-gate parity (T040)."""

from __future__ import annotations

import pytest

from backend.app.graph.evidence_gate import (
    EvidenceCandidate,
    EvidenceGateDecision,
    EvidenceGateInput,
    evaluate_evidence_gate,
)


TENANT_ID = "tenant-a"


def candidate(
    *,
    tenant_id: str = TENANT_ID,
    relevant: bool = True,
    retrievable: bool = True,
    source_type: str = "knowledge_base",
) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id="evidence-1",
        tenant_id=tenant_id,
        knowledge_base_id="kb-1",
        document_version="version-1",
        content="Employees may claim approved travel expenses.",
        relevance_score=0.95 if relevant else 0.01,
        retrievable=retrievable,
        source_type=source_type,
    )


@pytest.mark.parametrize("entrypoint", ["chat", "stream", "eval", "file_workflow"])
def test_reliable_current_tenant_evidence_satisfies_gate(entrypoint: str) -> None:
    result = evaluate_evidence_gate(
        EvidenceGateInput(
            tenant_id=TENANT_ID,
            query="Can I claim approved travel expenses?",
            candidates=[candidate()],
            retrieval_available=True,
            entrypoint=entrypoint,
        )
    )
    assert result.decision is EvidenceGateDecision.SUPPORTED
    assert result.accepted_evidence_ids == ("evidence-1",)


@pytest.mark.parametrize("entrypoint", ["chat", "stream", "eval", "file_workflow"])
def test_irrelevant_evidence_is_insufficient_for_every_entrypoint(entrypoint: str) -> None:
    result = evaluate_evidence_gate(
        EvidenceGateInput(
            tenant_id=TENANT_ID,
            query="What is the parental leave policy?",
            candidates=[candidate(relevant=False)],
            retrieval_available=True,
            entrypoint=entrypoint,
        )
    )
    assert result.decision is EvidenceGateDecision.INSUFFICIENT_EVIDENCE
    assert result.accepted_evidence_ids == ()


def test_retrieval_unavailable_fails_closed() -> None:
    result = evaluate_evidence_gate(
        EvidenceGateInput(
            tenant_id=TENANT_ID,
            query="Can I claim approved travel expenses?",
            candidates=[candidate()],
            retrieval_available=False,
            entrypoint="chat",
        )
    )
    assert result.decision is EvidenceGateDecision.INSUFFICIENT_EVIDENCE
    assert result.reason == "retrieval_unavailable"


def test_cross_tenant_evidence_never_satisfies_gate() -> None:
    result = evaluate_evidence_gate(
        EvidenceGateInput(
            tenant_id=TENANT_ID,
            query="Can I claim approved travel expenses?",
            candidates=[candidate(tenant_id="tenant-b")],
            retrieval_available=True,
            entrypoint="eval",
        )
    )
    assert result.decision is EvidenceGateDecision.INSUFFICIENT_EVIDENCE
    assert result.accepted_evidence_ids == ()


def test_non_authoritative_memory_never_satisfies_policy_evidence_gate() -> None:
    result = evaluate_evidence_gate(
        EvidenceGateInput(
            tenant_id=TENANT_ID,
            query="Can I claim approved travel expenses?",
            candidates=[candidate(source_type="memory")],
            retrieval_available=True,
            entrypoint="chat",
        )
    )
    assert result.decision is EvidenceGateDecision.INSUFFICIENT_EVIDENCE
    assert result.accepted_evidence_ids == ()


def test_non_retrievable_evidence_never_satisfies_gate() -> None:
    result = evaluate_evidence_gate(
        EvidenceGateInput(
            tenant_id=TENANT_ID,
            query="Can I claim approved travel expenses?",
            candidates=[candidate(retrievable=False)],
            retrieval_available=True,
            entrypoint="file_workflow",
        )
    )
    assert result.decision is EvidenceGateDecision.INSUFFICIENT_EVIDENCE
    assert result.accepted_evidence_ids == ()
