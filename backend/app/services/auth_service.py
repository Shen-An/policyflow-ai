"""Authentication service functions."""

from sqlmodel import Session

from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import verify_password
from backend.app.db.models import User
from backend.app.services.user_service import get_user_by_username


def authenticate_user(session: Session, username: str, password: str) -> User:
    user = get_user_by_username(session, username)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthenticationError("AUTH_INVALID_CREDENTIALS", "Invalid username or password")
    if user.status != "active":
        raise AuthenticationError("AUTH_USER_DISABLED", "User account is disabled")
    return user
