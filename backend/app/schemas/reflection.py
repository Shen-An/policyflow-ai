"""Schemas for the optional LLM reflection closed-loop (Critique → Improve).

Honest boundary: dual-prompt quality loop under a centralized Supervisor.
Not peer multi-agent debate; not a replacement for rule-based ComplianceAgent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CritiqueSeverity = Literal["error", "warning"]
CritiqueVerdict = Literal["PASS", "NEEDS_IMPROVEMENT"]
ReflectionStopReason = Literal[
    "pass",
    "max_rounds",
    "skipped",
    "hard_refuse",
    "disabled",
    "parse_error",
    "improve_empty",
    "error",
    "not_triggered",
]

CRITIQUE_DIMENSIONS = (
    "evidence_grounding",
    "citation_integrity",
    "numeric_fidelity",
    "completeness",
    "refuse_consistency",
    "structure_clarity",
)


class CritiqueIssue(BaseModel):
    id: str
    dimension: str
    severity: CritiqueSeverity = "warning"
    location: str = ""
    problem: str
    evidence_refs: list[int] = Field(default_factory=list)
    fix_hint: str = ""


class CritiqueResult(BaseModel):
    verdict: CritiqueVerdict
    summary: str = ""
    issues: list[CritiqueIssue] = Field(default_factory=list)


class ReflectionRound(BaseModel):
    round_index: int
    critique: CritiqueResult
    improved: bool = False
    answer_before: str = ""
    answer_after: str = ""


class ReflectionResult(BaseModel):
    triggered: bool = False
    skipped_reason: str | None = None
    rounds: list[ReflectionRound] = Field(default_factory=list)
    final_verdict: CritiqueVerdict | None = None
    stopped_reason: ReflectionStopReason = "skipped"
    max_rounds: int = 2
