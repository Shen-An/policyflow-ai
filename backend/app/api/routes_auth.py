"""Authentication API routes."""

from fastapi import APIRouter, Request

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.security import create_access_token
from backend.app.schemas.auth import AuthenticatedUser, LoginRequest, TokenResponse
from backend.app.schemas.user import UserRead
from backend.app.services.auth_service import authenticate_user
from backend.app.services.user_service import get_user_role_codes, to_user_read

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, request: Request, session: SessionDep) -> TokenResponse:
    user = authenticate_user(session, data.username, data.password)
    roles = get_user_role_codes(session, user.id)
    settings = request.app.state.settings
    return TokenResponse(
        access_token=create_access_token(user.id, settings),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=AuthenticatedUser(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            roles=roles,
        ),
    )


@router.get("/me", response_model=UserRead)
def get_me(user: CurrentUser, session: SessionDep) -> UserRead:
    return to_user_read(session, user)
