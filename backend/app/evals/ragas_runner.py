"""Optional RAGAS integration boundary."""

from importlib.util import find_spec
from typing import Literal

from pydantic import BaseModel, Field


class RagasEvaluationInput(BaseModel):
    question: str
    answer: str
    contexts: list[str]
    reference_answer: str | None = None


class RagasEvaluationResult(BaseModel):
    status: Literal["completed", "skipped", "failed"]
    metrics: dict[str, float] = Field(default_factory=dict)
    reason: str | None = None


class RagasRunner:
    async def run(
        self,
        data: RagasEvaluationInput,
        enabled: bool,
    ) -> RagasEvaluationResult:
        if not enabled:
            return RagasEvaluationResult(status="skipped", reason="disabled")
        if find_spec("ragas") is None:
            return RagasEvaluationResult(status="skipped", reason="missing_dependency")
        return RagasEvaluationResult(
            status="skipped",
            reason="evaluator_not_configured",
        )
