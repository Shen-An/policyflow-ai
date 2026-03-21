"""Skill API schemas."""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ProcessChecklistInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class PolicyCompareInput(BaseModel):
    policies: list[dict[str, Any]] = Field(min_length=2, max_length=20)


class SummaryInput(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=20_000)
    question: str | None = Field(default=None, min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def require_source_text(self) -> "SummaryInput":
        if not self.text and not self.question:
            raise ValueError("text or question is required")
        return self


class SkillRead(BaseModel):
    name: str
    version: str
    description: str
    enabled: bool
    risk_level: str
    input_schema: dict[str, Any]
    runnable: bool
    implemented: bool
    config_summary: dict[str, Any]


class SkillListResponse(BaseModel):
    items: list[SkillRead]


class SkillRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class SkillRunResponse(BaseModel):
    name: str
    output: dict[str, Any]
    audit_id: str
    request_id: str | None
