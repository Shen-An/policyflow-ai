"""Database engines, session factories and readiness probes.

PostgreSQL is the only production SQL authority. The async engine below is the
single writer path for business state; the synchronous engine is retained so
development and isolated tests can still run on SQLite, and so the Phase 2-9
legacy adapters keep working until their Stage 9 deletion gate is met.

Every pooled connection is bounded: pool size, overflow, checkout wait and
recycle all come from settings, and ``pool_pre_ping`` is enabled so a connection
killed by a database restart is not handed to a request.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Session, create_engine

from backend.app.core.config import Settings, get_settings

ALEMBIC_VERSION_TABLE = "alembic_version"
_REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI_PATH = _REPO_ROOT / "alembic.ini"
ALEMBIC_MIGRATIONS_DIR = _REPO_ROOT / "migrations"


def is_sqlite_url(database_url: str) -> bool:
    """Return True when the URL targets SQLite (development/test only)."""
    return make_url(database_url).get_backend_name() == "sqlite"


def configure_async_event_loop_policy() -> None:
    """Select an event loop policy that async psycopg can actually use.

    Windows defaults to ``ProactorEventLoop``, which psycopg 3 refuses to run in
    async mode. Entry points (application startup and the test harness) call
    this before any loop is created; on Linux it is a no-op.
    """
    if sys.platform != "win32":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:  # pragma: no cover - Python < 3.8
        return
    if not isinstance(asyncio.get_event_loop_policy(), selector_policy):
        asyncio.set_event_loop_policy(selector_policy())


def normalize_async_url(database_url: str) -> str:
    """Ensure a PostgreSQL URL selects an async-capable driver.

    ``postgresql://`` defaults to psycopg2, which has no async support. The
    enterprise contract requires psycopg 3, so a bare scheme is rewritten rather
    than silently falling back to a synchronous driver.
    """
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "postgresql" and url.drivername in {"postgresql", "postgresql+psycopg2"}:
        return str(url.set(drivername="postgresql+psycopg"))
    if backend == "sqlite" and not url.drivername.endswith("aiosqlite"):
        return str(url.set(drivername="sqlite+aiosqlite"))
    return database_url


def _prepare_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    # WAL allows concurrent readers, busy_timeout avoids "database is locked".
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def build_engine(database_url: str, echo: bool = False) -> Engine:
    """Build the synchronous engine used by development and legacy adapters."""
    _prepare_sqlite_directory(database_url)
    is_sqlite = is_sqlite_url(database_url)
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, echo=echo, connect_args=connect_args)
    if is_sqlite:
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


@lru_cache
def get_engine() -> Engine:
    """Process-wide synchronous engine (SQLite development and adapters)."""
    settings = get_settings()
    return build_engine(settings.DATABASE_URL, settings.DATABASE_ECHO)


def get_session() -> Generator[Session, None, None]:
    """Yield a synchronous session for legacy routes and adapters."""
    with Session(get_engine()) as session:
        yield session


def _async_engine_kwargs(settings: Settings, database_url: str) -> dict[str, Any]:
    """Return bounded pool settings for the async engine."""
    kwargs: dict[str, Any] = {
        "echo": settings.DATABASE_ECHO,
        "pool_pre_ping": settings.DATABASE_POOL_PRE_PING,
    }
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        # SQLite has no server-side pool; a static pool keeps one connection.
        return kwargs
    kwargs.update(
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
        pool_recycle=settings.DATABASE_POOL_RECYCLE_SECONDS,
        connect_args={"connect_timeout": int(settings.DATABASE_CONNECT_TIMEOUT_SECONDS)},
    )
    return kwargs


def build_async_engine(database_url: str, settings: Settings | None = None) -> AsyncEngine:
    """Build an async engine with bounded pooling and health checks."""
    resolved = settings or get_settings()
    normalized = normalize_async_url(database_url)
    _prepare_sqlite_directory(normalized)
    return create_async_engine(normalized, **_async_engine_kwargs(resolved, normalized))


@lru_cache
def get_async_engine() -> AsyncEngine:
    """Process-wide async engine for the PostgreSQL business authority."""
    settings = get_settings()
    return build_async_engine(settings.DATABASE_URL, settings)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory bound to the process-wide engine.

    ``expire_on_commit=False`` keeps returned ORM objects usable after commit,
    which matters because API handlers serialize them after the unit of work
    closes.
    """
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Open one async unit-of-work session, rolling back on error.

    Each unit of work owns its own transaction; there is no request-scoped
    session shared across tasks, so a failure cannot commit another request's
    partial work.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_async_engine() -> None:
    """Dispose pooled connections at shutdown and drop the cached engine."""
    try:
        engine = get_async_engine()
    except Exception:  # pragma: no cover - engine never built
        return
    await engine.dispose()
    get_async_engine.cache_clear()
    get_session_factory.cache_clear()


@dataclass(frozen=True)
class ReadinessReport:
    """Result of a readiness probe, safe to expose on a health endpoint."""

    ok: bool
    dialect: str
    schema_revision: str | None
    expected_revision: str | None
    reason: str | None = None


def expected_schema_revision() -> str | None:
    """Return the repository's Alembic head without touching the database."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(ALEMBIC_MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config).get_current_head()


async def check_database_ready(engine: AsyncEngine | None = None) -> ReadinessReport:
    """Probe connectivity and schema version without mutating the database.

    The API must not create or migrate schema at startup, so readiness is a
    read-only check: reachable server plus an applied Alembic revision.
    """
    resolved = engine or get_async_engine()
    dialect = resolved.dialect.name
    expected = expected_schema_revision()
    try:
        async with resolved.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        return ReadinessReport(
            ok=False,
            dialect=dialect,
            schema_revision=None,
            expected_revision=expected,
            reason=f"database unavailable: {type(exc).__name__}",
        )
    try:
        async with resolved.connect() as connection:
            exists = await connection.scalar(
                text("SELECT to_regclass(:table_name)"), {"table_name": ALEMBIC_VERSION_TABLE}
            )
            if exists is None:
                return ReadinessReport(
                    ok=False,
                    dialect=dialect,
                    schema_revision=None,
                    expected_revision=expected,
                    reason=(
                        "schema is not migrated: alembic_version table is absent; "
                        "run `alembic upgrade head`"
                    ),
                )
            revision = await connection.scalar(
                text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE} LIMIT 1")  # noqa: S608
            )
    except Exception as exc:
        return ReadinessReport(
            ok=False,
            dialect=dialect,
            schema_revision=None,
            expected_revision=expected,
            reason=f"schema probe failed: {type(exc).__name__}",
        )
    revision = str(revision) if revision else None
    if revision is None:
        return ReadinessReport(
            ok=False,
            dialect=dialect,
            schema_revision=None,
            expected_revision=expected,
            reason="schema is not migrated: alembic_version is empty",
        )
    if expected is not None and revision != expected:
        return ReadinessReport(
            ok=False,
            dialect=dialect,
            schema_revision=revision,
            expected_revision=expected,
            reason=f"schema revision {revision} does not match head {expected}",
        )
    return ReadinessReport(
        ok=True,
        dialect=dialect,
        schema_revision=revision,
        expected_revision=expected,
    )
