"""Skill registry API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.skill import (
    SkillListResponse,
    SkillRead,
    SkillRunRequest,
    SkillRunResponse,
)
from backend.app.skills.registry import SkillRegistry

router = APIRouter(prefix="/api/skills", tags=["skills"])
SysAdminUser = Annotated[User, Depends(require_roles("sys_admin"))]


@router.get("", response_model=SkillListResponse)
def get_skills(request: Request, user: CurrentUser, session: SessionDep) -> SkillListResponse:
    registry: SkillRegistry = request.app.state.skill_registry
    return registry.list(session)


@router.post("/{name}/enable", response_model=SkillRead)
def enable_skill(
    name: str,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> SkillRead:
    registry: SkillRegistry = request.app.state.skill_registry
    return registry.set_enabled(
        session,
        user,
        name,
        True,
        request.client.host if request.client else None,
    )


@router.post("/{name}/disable", response_model=SkillRead)
def disable_skill(
    name: str,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> SkillRead:
    registry: SkillRegistry = request.app.state.skill_registry
    return registry.set_enabled(
        session,
        user,
        name,
        False,
        request.client.host if request.client else None,
    )


@router.post("/{name}/run", response_model=SkillRunResponse)
async def run_skill(
    name: str,
    data: SkillRunRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> SkillRunResponse:
    registry: SkillRegistry = request.app.state.skill_registry
    return await registry.run(
        session,
        user,
        name,
        data.input,
        request.client.host if request.client else None,
    )
