"""System-administrator user management routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from backend.app.api.deps import SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.user import UserCreate, UserListResponse, UserRead, UserRoleUpdate
from backend.app.services.user_service import create_user, list_users, update_user_roles

router = APIRouter(prefix="/api/users", tags=["users"])
SysAdminUser = Annotated[User, Depends(require_roles("sys_admin"))]


@router.get("", response_model=UserListResponse)
def get_users(
    session: SessionDep,
    _: SysAdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: str | None = None,
) -> UserListResponse:
    return list_users(session, page, page_size, keyword)


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def post_user(data: UserCreate, session: SessionDep, _: SysAdminUser) -> UserRead:
    return create_user(session, data)


@router.put("/{user_id}/roles", response_model=UserRead)
def put_user_roles(
    user_id: str,
    data: UserRoleUpdate,
    session: SessionDep,
    _: SysAdminUser,
) -> UserRead:
    return update_user_roles(session, user_id, data.role_codes)
