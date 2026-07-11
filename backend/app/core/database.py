"""Backward-compatible database imports.

New code should import from :mod:`backend.app.db`.
"""

from backend.app.db.init_db import initialize_database
from backend.app.db.session import get_engine, get_session


def init_db() -> None:
    initialize_database()


engine = get_engine()

__all__ = ["engine", "get_engine", "get_session", "init_db"]
