"""Database engine and session management."""

from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, create_engine

from backend.app.core.config import get_settings


def _prepare_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def build_engine(database_url: str, echo: bool = False) -> Engine:
    _prepare_sqlite_directory(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=echo, connect_args=connect_args)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return build_engine(settings.DATABASE_URL, settings.DATABASE_ECHO)


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
