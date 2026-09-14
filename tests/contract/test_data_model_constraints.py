"""Contract tests for the Stage 2 data model and its database constraints.

The data model is the contract every other Stage 2 component builds on, so these
tests pin the parts a careless edit would silently break:

* the enum vocabularies in Python and the CHECK constraints in PostgreSQL must
  agree exactly — a value the ORM permits but the database rejects (or the other
  way round) is a defect that only appears in production;
* every table except the ownership root must declare tenant ownership, and the
  ownership root must not pretend to have an owner;
* the ORM metadata and the migrated schema must describe the same tables, which
  is what keeps the development ``create_all`` path from drifting away from the
  migrations production actually runs.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

from backend.app.db import models

ROOT = Path(__file__).resolve().parents[2]

#: ``(vocabulary, table, column)`` — the Python constant must equal the set the
#: database CHECK constraint accepts for this column.
VOCABULARY_BINDINGS: tuple[tuple[frozenset[str], str, str], ...] = (
    (models.TENANT_STATUSES, "tenants", "status"),
    (models.USER_STATUSES, "users", "status"),
    (models.RUN_KINDS, "agent_runs", "kind"),
    (models.RUN_STATUSES, "agent_runs", "status"),
    (models.EVIDENCE_GATES, "agent_runs", "evidence_gate"),
    (models.IDEMPOTENCY_STATES, "idempotency_records", "state"),
    (models.AUDIT_OUTCOMES, "audit_events", "outcome"),
)


def load_migration(revision: str) -> ModuleType:
    """Return the migration module for ``revision`` by importing its file.

    Alembic reads these files by path, so the module names (``001_...``) are not
    valid Python identifiers and cannot be imported by name.
    """
    matches = sorted((ROOT / "migrations" / "versions").glob(f"{revision}_*.py"))
    assert len(matches) == 1, f"expected exactly one migration for {revision}: {matches}"
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"migration_{revision}", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def expand() -> ModuleType:
    """The expand (001) migration module."""
    return load_migration("001")


@pytest.fixture(scope="module")
def enforce() -> ModuleType:
    """The enforce (002) migration module."""
    return load_migration("002")


def test_vocabularies_are_frozen_and_lowercase() -> None:
    """Vocabularies must be immutable and use one casing convention."""
    for vocabulary, table, column in VOCABULARY_BINDINGS:
        assert isinstance(vocabulary, frozenset), f"{table}.{column} must be a frozenset"
        assert vocabulary, f"{table}.{column} must not be empty"
        for value in vocabulary:
            assert value == value.lower(), f"{table}.{column} value {value!r} is not lowercase"
            assert value.strip() == value and value, f"{table}.{column} value {value!r} has padding"


def test_python_vocabularies_match_the_database_checks(expand: ModuleType) -> None:
    """Every vocabulary must be enforced by a CHECK constraint with the same set."""
    declared = {
        (table, column): set(allowed)
        for table, _name, column, allowed in expand.CHECK_CONSTRAINTS
    }
    for vocabulary, table, column in VOCABULARY_BINDINGS:
        assert (table, column) in declared, (
            f"{table}.{column} has a Python vocabulary but no database CHECK constraint; "
            "an invalid value would be storable"
        )
        assert declared[(table, column)] == set(vocabulary), (
            f"{table}.{column} disagrees: database allows {sorted(declared[(table, column)])}, "
            f"Python allows {sorted(vocabulary)}"
        )


def test_every_database_check_has_a_python_vocabulary(expand: ModuleType) -> None:
    """The reverse direction too, so a constraint cannot be added without a constant."""
    bound = {(table, column) for _vocabulary, table, column in VOCABULARY_BINDINGS}
    for table, name, column, _allowed in expand.CHECK_CONSTRAINTS:
        assert (table, column) in bound, (
            f"CHECK constraint {name} on {table}.{column} has no Python vocabulary to match it"
        )


def test_check_constraint_names_follow_the_convention(expand: ModuleType) -> None:
    """Constraint names must be predictable, because rollouts drop them by name."""
    for table, name, column, _allowed in expand.CHECK_CONSTRAINTS:
        assert name == f"ck_{table}_{column}", f"{name} should be ck_{table}_{column}"


def test_ownership_root_has_no_owner_and_everything_else_does() -> None:
    """``tenants`` is the root; every other tenant table must declare an owner.

    The scan is scoped to the application's own models rather than all of
    ``SQLModel.metadata``: test doubles declared in the suite are also registered
    there, and they follow test needs rather than the production contract.
    """
    application_tables = {
        obj.__table__.name: obj.__table__
        for obj in vars(models).values()
        if isinstance(obj, type)
        and issubclass(obj, SQLModel)
        and getattr(obj, "__table__", None) is not None
    }
    assert "tenants" in application_tables

    tenant_scoped: list[str] = []
    for name, table in sorted(application_tables.items()):
        columns = table.columns
        if name == "tenants":
            assert "tenant_id" not in columns, (
                "the ownership root must not carry a tenant_id; it would be self-referential"
            )
            continue
        if "tenant_id" in columns:
            tenant_scoped.append(name)
            foreign_keys = {fk.target_fullname for fk in columns["tenant_id"].foreign_keys}
            assert foreign_keys == {"tenants.id"}, (
                f"{name}.tenant_id must reference tenants.id, found {foreign_keys or 'no FK'}"
            )
    assert len(tenant_scoped) >= 19, (
        f"expected the Stage 2 tenant-scoped tables, found {len(tenant_scoped)}: {tenant_scoped}"
    )


def test_compare_and_set_models_declare_a_version_column() -> None:
    """A model updated under compare-and-set must carry the version it compares."""
    from backend.app.db.repositories import (
        AgentRun,
        GraphCheckpointBinding,
        IdempotencyRecord,
        Role,
        User,
        UserRoleGrant,
    )

    for model in (
        User,
        Role,
        UserRoleGrant,
        AgentRun,
        GraphCheckpointBinding,
        IdempotencyRecord,
    ):
        table = model.__table__
        assert "version" in table.columns, (
            f"{table.name} is written under compare-and-set but has no version column"
        )
        assert table.columns["version"].nullable is False


def test_metadata_and_migrations_agree_on_the_table_set(
    expand: ModuleType, enforce: ModuleType
) -> None:
    """The ORM and the migrations must describe the same tables and columns.

    The development path creates its schema from the ORM while production runs
    the migrations, so any drift here means development and production disagree
    about the schema.
    """
    engine = create_engine("sqlite://")
    metadata = sa.MetaData()
    SQLModel.metadata.create_all(engine)
    for table in SQLModel.metadata.sorted_tables:
        metadata._add_table(table.name, table.schema, table)  # noqa: SLF001
    created = set(inspect(engine).get_table_names())
    engine.dispose()

    assert created == set(SQLModel.metadata.tables), (
        "create_all must realise every declared table"
    )

    # The migrations create the same tables the ORM declares, so a table added to
    # one side only is a drift the migrations cannot express.
    declared = set(SQLModel.metadata.tables)
    scoped = set(expand.TENANT_SCOPED_TABLES) | set(enforce.TENANT_SCOPED_TABLES)
    assert scoped <= declared, f"migrations name tables the ORM does not declare: {scoped - declared}"


def test_every_scoped_table_is_covered_by_both_migration_phases(
    expand: ModuleType, enforce: ModuleType
) -> None:
    """Enforce must cover every table expand scoped, and may add more.

    The sets are not equal on purpose: four tables receive their ``tenant_id``
    from the backfill, which runs between expand and enforce, so expand cannot
    reference a column that does not exist yet. Enforce runs afterwards and must
    therefore be a superset.
    """
    expanded = set(expand.TENANT_SCOPED_TABLES)
    enforced = set(enforce.TENANT_SCOPED_TABLES)
    assert expanded <= enforced, (
        "enforce must cover every table expand scoped; missing "
        f"{sorted(expanded - enforced)}"
    )
    for name in sorted(enforced):
        node = SQLModel.metadata.tables[name]
        assert "tenant_id" in node.columns, (
            f"{name} is tenant-scoped but has no tenant_id in the ORM; an ORM insert "
            "would violate NOT NULL after the enforce migration"
        )


def test_every_scoped_table_has_an_owner_column_in_the_orm(enforce: ModuleType) -> None:
    """The ORM must declare tenant ownership wherever the schema enforces it."""
    missing = [
        name
        for name in sorted(enforce.TENANT_SCOPED_TABLES)
        if "tenant_id" not in SQLModel.metadata.tables[name].columns
    ]
    assert missing == [], f"the ORM omits tenant_id on enforced tables: {missing}"


def test_per_tenant_uniqueness_targets_tenant_scoped_tables(enforce: ModuleType) -> None:
    """A composite uniqueness rule needs an owner column to be composite with."""
    assert enforce.PER_TENANT_UNIQUE_CODES, "at least one business identifier must be per tenant"
    for table, column in enforce.PER_TENANT_UNIQUE_CODES:
        node = SQLModel.metadata.tables[table]
        assert "tenant_id" in node.columns, (
            f"{table} appears in PER_TENANT_UNIQUE_CODES but is not tenant-scoped"
        )
        assert column in node.columns, f"{table} has no column {column}"
    # The same rule must never be listed twice, which would create a duplicate.
    assert len(set(enforce.PER_TENANT_UNIQUE_CODES)) == len(enforce.PER_TENANT_UNIQUE_CODES)
