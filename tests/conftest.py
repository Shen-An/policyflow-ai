"""Shared pytest fixtures for the enterprise PostgreSQL suites.

Phase 2 (Stage 2) tests require a *real* PostgreSQL server because the migration,
tenant-isolation and multi-instance gates cannot be proven on SQLite. The
fixtures here therefore talk to the compose-managed PostgreSQL from
``infra/dev/compose.yaml`` instead of substituting an in-process database.

Only explicitly requested fixtures do work: importing this module has no side
effects, so the existing SQLite-backed suites keep running unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.db.session import configure_async_event_loop_policy

# Async psycopg refuses Windows' default Proactor loop; the policy must be
# selected before pytest-asyncio creates its first loop.
configure_async_event_loop_policy()

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL_ENV = "POLICYFLOW_TEST_DATABASE_URL"
# Docker Compose loads this file as the project env file; the suites read the
# same knobs so the tests and the running stack can never disagree.
INFRA_ENV_FILE = REPO_ROOT / "infra" / "dev" / ".env"

# The database the server always ships with, used only to CREATE DATABASE.
MAINTENANCE_DATABASE = "policyflow"
TEST_DATABASE_NAME = "policyflow_test"

# Compose defaults from infra/dev/compose.yaml, used when the env file is absent.
_COMPOSE_DEFAULTS = {
    "POSTGRES_USER": "policyflow",
    "POSTGRES_PASSWORD": "policyflow-dev",
    "POSTGRES_PORT": "5432",
}


def _read_env_file() -> dict[str, str]:
    """Read simple KEY=VALUE pairs from the infra env file without importing it.

    Only the POSTGRES_* knobs are consumed, so no application secret is exposed.
    """
    values: dict[str, str] = {}
    if not INFRA_ENV_FILE.exists():
        return values
    for raw in INFRA_ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _build_default_test_url() -> str:
    """Compose the test URL from the same knobs the dev stack uses.

    Deriving it instead of hard-coding 5432 keeps the suites working when the
    host port has to move (for example when another service already owns 5432).
    """
    file_values = _read_env_file()

    def resolve(key: str) -> str:
        return os.environ.get(key) or file_values.get(key) or _COMPOSE_DEFAULTS[key]

    user = resolve("POSTGRES_USER")
    password = resolve("POSTGRES_PASSWORD")
    port = resolve("POSTGRES_PORT")
    return (
        f"postgresql+psycopg://{user}:{password}@127.0.0.1:{port}/{TEST_DATABASE_NAME}"
    )


def test_database_url() -> str:
    """Return the PostgreSQL URL every enterprise suite runs against."""
    override = os.environ.get(TEST_DATABASE_URL_ENV)
    if override and override.strip():
        return override.strip()
    return _build_default_test_url()


def _sync_dsn(url: str, database: str) -> str:
    """Build a libpq DSN for the same host/credentials but another database."""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    return str(
        parsed.set(drivername="postgresql", database=database).render_as_string(
            hide_password=False
        )
    )


def _database_name(url: str) -> str:
    from sqlalchemy.engine import make_url

    name = make_url(url).database
    if not name:
        raise RuntimeError("Test database URL must include a database name")
    return name


def _ensure_database_exists(url: str) -> bool:
    """Create the test database when absent; return False when the server is down.

    Returning False instead of raising lets the caller skip with an actionable
    message rather than producing a confusing connection traceback.
    """
    import psycopg

    target = _database_name(url)
    try:
        with psycopg.connect(_sync_dsn(url, MAINTENANCE_DATABASE), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
                if cur.fetchone() is None:
                    cur.execute(f'CREATE DATABASE "{target}"')
    except psycopg.OperationalError as exc:  # pragma: no cover - environment gate
        pytest.skip(f"PostgreSQL is not reachable for enterprise suites: {exc}")
    return True


def _drop_schema(url: str) -> None:
    """Drop and recreate the ``public`` schema so a run starts from a clean slate."""
    import psycopg

    with psycopg.connect(_sync_dsn(url, _database_name(url)), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            # The migration role must never be able to bypass RLS.
            cur.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")


def alembic_upgrade(url: str, revision: str = "head") -> subprocess.CompletedProcess[str]:
    """Apply Alembic migrations in a subprocess against ``url``.

    A subprocess is used deliberately: ``Settings`` is ``lru_cache``d process-wide
    and Alembic's env.py reads it, so running in-process would let one suite's
    database URL leak into another's.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ENVIRONMENT"] = "test"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_legacy_backfill(url: str) -> subprocess.CompletedProcess[str]:
    """Attribute pre-tenant rows to the legacy tenant, as production must.

    The ``enforce`` revision refuses to run until this has reconciled every
    tenant-scoped table, so the staged chain is expand -> backfill -> enforce
    rather than a single ``upgrade head``.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ENVIRONMENT"] = "test"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "migrations.backfill_legacy_tenant"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@dataclass(frozen=True)
class PgTestDatabase:
    """A migrated PostgreSQL database plus the URL used to reach it."""

    url: str

    @property
    def database(self) -> str:
        return _database_name(self.url)


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Session-wide PostgreSQL URL, skipping cleanly when no server is running."""
    url = test_database_url()
    _ensure_database_exists(url)
    return url


@pytest.fixture(scope="session")
def pg_migrated(pg_url: str) -> PgTestDatabase:
    """A PostgreSQL database with the full migration chain applied once per session.

    The chain is applied in the order production must follow it: the additive
    ``expand`` revision, then the legacy-tenant backfill, then ``enforce``. A
    single ``upgrade head`` is deliberately NOT used because enforce refuses to
    tighten ownership before the backfill has reconciled it.
    """
    _drop_schema(pg_url)
    expand = alembic_upgrade(pg_url, "001")
    if expand.returncode != 0:  # pragma: no cover - reported through the failure
        pytest.fail(
            "alembic upgrade 001 failed against PostgreSQL\n"
            f"stdout:\n{expand.stdout}\nstderr:\n{expand.stderr}"
        )
    backfill = run_legacy_backfill(pg_url)
    if backfill.returncode != 0:  # pragma: no cover - reported through the failure
        pytest.fail(
            "the legacy tenant backfill failed against PostgreSQL\n"
            f"stdout:\n{backfill.stdout}\nstderr:\n{backfill.stderr}"
        )
    result = alembic_upgrade(pg_url)
    if result.returncode != 0:  # pragma: no cover - reported through the failure
        pytest.fail(
            "alembic upgrade head failed against PostgreSQL\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return PgTestDatabase(url=pg_url)


@pytest.fixture
def postgres_url(pg_url: str) -> str:
    """Function-scoped alias so tests can opt into plain connectivity checks."""
    return pg_url


def truncate_all_tables(url: str) -> None:
    """Remove all rows from the migrated schema, preserving Alembic state."""
    import psycopg

    with psycopg.connect(_sync_dsn(url, _database_name(url)), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename <> 'alembic_version'
                """
            )
            tables = [row[0] for row in cur.fetchall()]
            if tables:
                quoted = ", ".join(f'public."{name}"' for name in tables)
                cur.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")


@pytest.fixture
def clean_db(pg_migrated: PgTestDatabase) -> Iterator[PgTestDatabase]:
    """A migrated database truncated before and after the test."""
    truncate_all_tables(pg_migrated.url)
    yield pg_migrated
    truncate_all_tables(pg_migrated.url)


@pytest.fixture
def unique_suffix() -> Callable[[], str]:
    """Return short unique tokens so tests never collide on unique columns."""

    def make(prefix: str = "sfx") -> str:
        return f"{prefix}-{uuid4().hex[:12]}"

    return make


def _with_database(url: str, name: str) -> str:
    """Return ``url`` pointed at database ``name``, keeping its driver."""
    from sqlalchemy.engine import make_url

    return make_url(url).set(database=name).render_as_string(hide_password=False)


@contextmanager
def scratch_database(base_url: str, name: str) -> Iterator[str]:
    """Yield a private database and drop it afterwards.

    Tests that drive the migration chain themselves cannot share the session
    database: they drop and rebuild its schema, and ``enforce`` refuses to run
    until their own backfill has reconciled. A throwaway database keeps that
    state from leaking into the rest of the suite.
    """
    import psycopg

    maintenance = _sync_dsn(base_url, MAINTENANCE_DATABASE)
    target = _with_database(base_url, name)
    with psycopg.connect(maintenance, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield target
    finally:
        with psycopg.connect(maintenance, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
