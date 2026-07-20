"""Shared Agent pipeline result models and per-turn shared state (blackboard)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.schemas.chat import ComplianceResult, PlanOption, PlanStep, RouterResult
from backend.app.schemas.reflection import ReflectionResult
from backend.app.schemas.retrieval import RetrievalResult


class AnswerResult(BaseModel):
    answer: str
    confidence_score: float
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)


class MemoryWorkingSet(BaseModel):
    """Request-scoped memory assembly for one chat turn."""

    history: list[dict[str, Any]] = Field(default_factory=list)
    fixed_memories: list[dict[str, Any]] = Field(default_factory=list)
    recalled_memories: list[dict[str, Any]] = Field(default_factory=list)
    rolling_summary: str = ""
    memory_ids: list[str] = Field(default_factory=list)


TurnStatus = Literal["running", "completed", "awaiting_plan_selection", "cancelled"]
ErrorSeverity = Literal["info", "warning", "error"]
ReasoningMode = Literal["cot_direct", "cot_steps", "tot_select"]


class TurnError(BaseModel):
    """One failure / degrade event written into the shared turn ledger."""

    code: str
    message: str
    source: str
    step_id: str | None = None
    severity: ErrorSeverity = "error"
    details: dict[str, Any] = Field(default_factory=dict)


class TurnState(BaseModel):
    """
    Per-turn shared record (blackboard) for the centralized Supervisor.

    Topology stays static: stages write here; they do not peer-message each other.
    Success and failure both land in this object — especially ``errors``.
    """

    question: str = ""
    status: TurnStatus = "running"
    reasoning_mode: ReasoningMode = "cot_direct"
    router_result: RouterResult | None = None
    plan: list[PlanStep] = Field(default_factory=list)
    plan_options: list[PlanOption] = Field(default_factory=list)
    retrieval_result: RetrievalResult | None = None
    skill_results: list[dict[str, Any]] = Field(default_factory=list)
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    answer_result: AnswerResult | None = None
    compliance: ComplianceResult | None = None
    # Optional Critique→Improve closed-loop snapshot (high-stakes turns only).
    reflection: ReflectionResult | None = None
    errors: list[TurnError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    waves: list[list[str]] = Field(default_factory=list)
    parallel_used: bool = False
    used_l2: bool = False

    def record_error(
        self,
        *,
        code: str,
        message: str,
        source: str,
        step_id: str | None = None,
        severity: ErrorSeverity = "error",
        details: dict[str, Any] | None = None,
    ) -> TurnError:
        err = TurnError(
            code=(code or "UNKNOWN")[:64],
            message=(message or "")[:500],
            source=(source or "pipeline")[:64],
            step_id=(step_id[:32] if step_id else None),
            severity=severity,
            details=dict(details or {}),
        )
        self.errors.append(err)
        if severity in {"warning", "info"} and err.code not in self.warnings:
            self.warnings.append(err.code)
        return err

    def record_step_outcome(
        self,
        *,
        step_id: str,
        kind: str,
        status: str,
        message: str,
        source: str = "PlanExecutor",
        extra: dict[str, Any] | None = None,
    ) -> TurnError | None:
        """Map a plan step terminal status into the errors ledger when non-success."""
        if status == "success":
            return None
        if status == "skipped":
            severity: ErrorSeverity = "info"
            code = f"STEP_SKIPPED_{kind.upper()}"
        elif status == "error":
            severity = "error"
            code = f"STEP_ERROR_{kind.upper()}"
        else:
            severity = "warning"
            code = f"STEP_{status.upper()}_{kind.upper()}"
        return self.record_error(
            code=code,
            message=message or status,
            source=source,
            step_id=step_id,
            severity=severity,
            details={"kind": kind, "status": status, **(extra or {})},
        )

    def has_blocking_errors(self) -> bool:
        return any(e.severity == "error" for e in self.errors)

    def to_pipeline_result(self) -> PipelineResult:
        empty_retrieval = RetrievalResult(evidence=[], trace=[], latency_ms=0)
        empty_answer = AnswerResult(answer="", confidence_score=0.0)
        empty_compliance = ComplianceResult(passed=True, warnings=[])
        router = self.router_result or RouterResult(domain="general")
        result_status: Literal["completed", "awaiting_plan_selection", "cancelled"]
        if self.status == "awaiting_plan_selection":
            result_status = "awaiting_plan_selection"
        elif self.status == "cancelled":
            result_status = "cancelled"
        else:
            result_status = "completed"
        return PipelineResult(
            router_result=router,
            retrieval_result=self.retrieval_result or empty_retrieval,
            answer_result=self.answer_result or empty_answer,
            compliance=self.compliance or empty_compliance,
            suggested_skills=list(self.suggested_skills),
            skill_results=list(self.skill_results),
            plan=list(self.plan),
            status=result_status,
            plan_options=list(self.plan_options),
            reasoning_mode=self.reasoning_mode,
            errors=list(self.errors),
            reflection=self.reflection,
            turn_state=self,
        )


class PipelineResult(BaseModel):
    router_result: RouterResult
    retrieval_result: RetrievalResult
    answer_result: AnswerResult
    compliance: ComplianceResult
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    skill_results: list[dict[str, Any]] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    # Dual-request ToT HITL: awaiting_plan_selection stops before retrieve.
    status: Literal["completed", "awaiting_plan_selection", "cancelled"] = "completed"
    plan_options: list[PlanOption] = Field(default_factory=list)
    reasoning_mode: ReasoningMode = "cot_direct"
    # Centralized error ledger (also mirrored on TurnState when present).
    errors: list[TurnError] = Field(default_factory=list)
    # Optional Critique→Improve loop result (high-stakes only; may be skipped).
    reflection: ReflectionResult | None = None
    # Optional full blackboard snapshot for diagnostics / resume inspection.
    turn_state: TurnState | None = None
