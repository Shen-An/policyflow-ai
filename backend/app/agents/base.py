"""Shared Agent pipeline result models."""

from typing import Any

from pydantic import BaseModel, Field

from backend.app.schemas.chat import ComplianceResult, RouterResult
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


class PipelineResult(BaseModel):
    router_result: RouterResult
    retrieval_result: RetrievalResult
    answer_result: AnswerResult
    compliance: ComplianceResult
    suggested_skills: list[dict[str, str]] = Field(default_factory=list)
    skill_results: list[dict[str, Any]] = Field(default_factory=list)
