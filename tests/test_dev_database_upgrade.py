"""Regression tests for upgrading an existing development SQLite database.

The development database is a file that survives code changes, so a column added
to a model but not to the in-place upgrade path makes the service unstartable for
every developer who already has one. That is precisely what happened: the ORM had
gained ``roles.actions``, ``roles.updated_at``, ``users.external_subject``,
``tenant_id`` on fifteen tables and ``version``, while the upgrade list recorded
none of them, and startup failed with ``no such column``.

These tests reproduce that state instead of trusting the list to stay complete.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, SQLModel, select

from backend.app.core.config import get_settings
from backend.app.db import models  # noqa: F401  registers the metadata
from backend.app.db.init_db import initialize_database
from backend.app.db.models import LEGACY_TENANT_CODE, Role, Tenant


@pytest.fixture
def dev_database(tmp_path: Path) -> Iterator[str]:
    """A development-path SQLite database URL inside the test's temp directory."""
    url = f"sqlite:///{(tmp_path / 'dev.db').as_posix()}"
    engine = create_engine(url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    yield url


def columns_of(url: str, table: str) -> set[str]:
    """Return the column names a table currently has."""
    engine = create_engine(url)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def run_initialize(url: str) -> None:
    """Run the development startup path against ``url``."""
    engine = create_engine(url)
    try:
        initialize_database(engine, get_settings())
    finally:
        engine.dispose()


def test_a_database_missing_added_columns_is_upgraded_in_place(dev_database: str) -> None:
    """A database without later columns must be upgraded, not refused.

    ``roles.actions`` and ``roles.updated_at`` are exactly the columns whose
    absence produced ``no such column: roles.actions`` at startup. They are
    dropped rather than the whole table because the seed must still find its
    existing rows afterwards.
    """
    engine = create_engine(dev_database)
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE roles DROP COLUMN actions"))
            connection.execute(text("ALTER TABLE roles DROP COLUMN updated_at"))
    finally:
        engine.dispose()

    assert "actions" not in columns_of(dev_database, "roles")
    assert "updated_at" not in columns_of(dev_database, "roles")

    run_initialize(dev_database)

    restored = columns_of(dev_database, "roles")
    assert {"actions", "updated_at"} <= restored, (
        f"the in-place upgrade must restore the missing columns, found {sorted(restored)}"
    )


def load_migration(revision: str):
    """Return a migration module by revision, since its filename is not an identifier."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    matches = sorted((root / "migrations" / "versions").glob(f"{revision}_*.py"))
    assert len(matches) == 1, matches
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage_two_columns_can_be_added_to_an_existing_database() -> None:
    """Every column this work added to a pre-existing table must be addable.

    Not every column in the metadata is addable, and that is fine: a NOT NULL
    business column such as ``roles.code`` has always been part of the schema, so
    a database lacking it is not one this upgrade path has to serve. The tables
    that genuinely predate tenancy are exactly the ones the expand migration made
    tenant-scoped — the Stage 2 tables it also created are built wholesale by
    ``create_all`` and never need an ``ALTER``.
    """
    from backend.app.db.init_db import _sqlite_add_column_clause

    pre_existing = [
        name
        for name in load_migration("001").TENANT_SCOPED_TABLES
        # A nullable ``tenant_id`` marks a table that already existed and only
        # gained ownership in the expand phase; the Stage 2 tables own their rows
        # from birth (NOT NULL) and are created wholesale by ``create_all``.
        if SQLModel.metadata.tables[name].columns["tenant_id"].nullable
    ]
    # Eight is the number the ORM explicitly models as pre-existing (nullable
    # ownership). The guard exists so the list cannot quietly become empty and
    # turn this test into a no-op.
    assert len(pre_existing) >= 8, pre_existing

    required: list[tuple[str, str]] = [(name, "tenant_id") for name in pre_existing]
    # The tables whose compare-and-set support arrived with this work, plus the
    # three legacy columns whose absence produced ``no such column`` at startup.
    required += [
        ("users", "version"),
        ("roles", "version"),
        ("roles", "actions"),
        ("roles", "updated_at"),
        ("users", "external_subject"),
    ]

    unrenderable = [
        f"{table}.{column}"
        for table, column in required
        if _sqlite_add_column_clause(SQLModel.metadata.tables[table].columns[column]) is None
    ]
    assert unrenderable == [], (
        "these columns cannot be added to an existing development database, which "
        f"makes the service unstartable for anyone who has one: {unrenderable}"
    )


def test_pre_tenancy_rows_are_adopted_instead_of_duplicated(dev_database: str) -> None:
    """Rows written before tenancy must be adopted, not orphaned.

    The seed looks a role up by ``(tenant_id, code)``. An old row has no tenant,
    so without adoption the seed would insert a second ``employee`` and the
    pre-tenant global unique index on ``roles.code`` would abort startup.
    """
    engine = create_engine(dev_database)
    try:
        with Session(engine) as session:
            session.add(
                Role(
                    tenant_id=None,
                    code="employee",
                    name="legacy employee",
                    description="written before tenancy existed",
                )
            )
            session.commit()
    finally:
        engine.dispose()

    run_initialize(dev_database)

    engine = create_engine(dev_database)
    try:
        with Session(engine) as session:
            legacy = session.exec(
                select(Tenant).where(Tenant.code == LEGACY_TENANT_CODE)
            ).first()
            assert legacy is not None, "pre-tenancy rows require a legacy tenant"

            roles = session.exec(select(Role).where(Role.code == "employee")).all()
            assert len(roles) == 1, (
                f"the existing role must be adopted, not duplicated: {[r.id for r in roles]}"
            )
            assert roles[0].tenant_id == legacy.id
            # The pre-existing row is kept as it was; the seed does not overwrite it.
            assert roles[0].name == "legacy employee"
    finally:
        engine.dispose()
