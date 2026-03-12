"""Shared Agent pipeline result models."""

from pydantic import BaseModel, Field

from backend.app.schemas.chat import ComplianceResult, RouterResult
from backend.app.schemas.retrieval import RetrievalResult


class AnswerResult(BaseModel):
    answer: str
    confidence_score: float


class PipelineResult(BaseModel):
    router_result: RouterResult
    retrieval_result: RetrievalResult
    answer_result: AnswerResult
    compliance: ComplianceResult
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
