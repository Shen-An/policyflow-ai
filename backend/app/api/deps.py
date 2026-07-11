"""FastAPI dependencies for database sessions and authentication."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import decode_access_token
from backend.app.db.models import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_db_session(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db_session)]


def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    payload = decode_access_token(credentials.credentials, request.app.state.settings)
    user = session.get(User, payload["sub"])
    if user is None:
        raise AuthenticationError("AUTH_INVALID_TOKEN", "Token user no longer exists")
    if user.status != "active":
        raise AuthenticationError("AUTH_USER_DISABLED", "User account is disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
