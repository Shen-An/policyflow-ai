"""Tool handler contracts."""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlmodel import Session

from backend.app.db.models import User

ToolHandler = Callable[[Session, User, dict[str, Any]], Awaitable[dict[str, Any]]]
