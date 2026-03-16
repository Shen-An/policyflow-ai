"""Role-based permission helpers."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from backend.app.api.deps import CurrentUser, get_db_session
from backend.app.core.exceptions import PermissionDeniedError
from backend.app.db.models import User
from backend.app.services.user_service import get_user_role_codes


def has_any_role(session: Session, user: User, required_roles: set[str]) -> bool:
    return bool(set(get_user_role_codes(session, user.id)) & required_roles)


def require_roles(*role_codes: str) -> Callable[..., User]:
    required_roles = set(role_codes)

    def dependency(
        user: CurrentUser,
        session: Annotated[Session, Depends(get_db_session)],
    ) -> User:
        if not has_any_role(session, user, required_roles):
            raise PermissionDeniedError(
                "Required role is missing",
                {"required_roles": sorted(required_roles)},
            )
        return user

    return dependency
