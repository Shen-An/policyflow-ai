"""Database-aware Skill registry."""

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_request_id
from backend.app.core.redaction import redact_sensitive
from backend.app.db.models import Skill, User, utc_now
from backend.app.schemas.skill import (
    PolicyCompareInput,
    ProcessChecklistInput,
    SkillListResponse,
    SkillRead,
    SkillRunResponse,
    SummaryInput,
)
from backend.app.services.audit_service import record_audit
from backend.app.skills.base import SkillHandler
from backend.app.skills.handlers.builtin import (
    PolicyCompareSkill,
    ProcessChecklistSkill,
    SummarySkill,
)


class SkillRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, SkillHandler] = {
            "process_checklist": ProcessChecklistSkill(),
            "policy_compare": PolicyCompareSkill(),
            "summary": SummarySkill(),
        }
        self.input_models: dict[str, type[BaseModel]] = {
            "process_checklist": ProcessChecklistInput,
            "policy_compare": PolicyCompareInput,
            "summary": SummaryInput,
        }

    def _to_read(self, skill: Skill) -> SkillRead:
        input_model = self.input_models.get(skill.name)
        implemented = skill.name in self.handlers and input_model is not None
        return SkillRead(
            **skill.model_dump(),
            input_schema=input_model.model_json_schema() if input_model else {},
            runnable=skill.enabled and implemented,
            implemented=implemented,
            config_summary=redact_sensitive(skill.config),
        )

    def list(self, session: Session) -> SkillListResponse:
        skills = session.exec(select(Skill).order_by(Skill.name)).all()
        return SkillListResponse(items=[self._to_read(skill) for skill in skills])

    def set_enabled(
        self,
        session: Session,
        user: User,
        name: str,
        enabled: bool,
        ip_address: str | None = None,
    ) -> SkillRead:
        skill = session.exec(select(Skill).where(Skill.name == name)).first()
        if skill is None:
            raise ApplicationError("SKILL_NOT_FOUND", "Skill not found", 404)
        previous_enabled = skill.enabled
        skill.enabled = enabled
        skill.updated_at = utc_now()
        session.add(skill)
        record_audit(
            session,
            action="skill.enable" if enabled else "skill.disable",
            target_type="skill",
            actor_id=user.id,
            target_id=skill.id,
            detail={
                "name": skill.name,
                "previous_enabled": previous_enabled,
                "enabled": enabled,
            },
            ip_address=ip_address,
        )
        session.commit()
        session.refresh(skill)
        return self._to_read(skill)

    async def run(
        self,
        session: Session,
        user: User,
        name: str,
        payload: dict[str, Any],
        ip_address: str | None = None,
    ) -> SkillRunResponse:
        skill = session.exec(select(Skill).where(Skill.name == name)).first()
        if skill is None:
            raise ApplicationError("SKILL_NOT_FOUND", "Skill not found", 404)
        if not skill.enabled:
            raise ApplicationError("SKILL_DISABLED", "Skill is disabled", 409)
        handler = self.handlers.get(name)
        input_model = self.input_models.get(name)
        if handler is None or input_model is None:
            raise ApplicationError("SKILL_NOT_IMPLEMENTED", "Skill handler is not implemented", 501)
        try:
            validated_payload = input_model.model_validate(payload).model_dump()
        except ValidationError as exc:
            raise ApplicationError(
                "SKILL_INPUT_INVALID",
                "Skill input validation failed",
                422,
                exc.errors(include_context=False, include_input=False),
            ) from exc
        try:
            output = await handler.run(validated_payload)
        except Exception:
            record_audit(
                session,
                action="skill.run",
                target_type="skill",
                actor_id=user.id,
                target_id=skill.id,
                detail={
                    "name": skill.name,
                    "status": "failed",
                    "input_summary": redact_sensitive(validated_payload),
                },
                ip_address=ip_address,
            )
            session.commit()
            raise
        audit = record_audit(
            session,
            action="skill.run",
            target_type="skill",
            actor_id=user.id,
            target_id=skill.id,
            detail={
                "name": skill.name,
                "status": "success",
                "input_summary": redact_sensitive(validated_payload),
            },
            ip_address=ip_address,
        )
        session.commit()
        return SkillRunResponse(
            name=name,
            output=output,
            audit_id=audit.id,
            request_id=get_request_id(),
        )
