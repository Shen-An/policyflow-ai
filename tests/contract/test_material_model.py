"""T073 [US1] contract tests for the Stage-5 material and evidence-storage model.

Stage 5 splits one concern per store (``data-model.md`` "Cross-Entity
Invariants" #5): PostgreSQL owns business state, the object store owns bytes,
Milvus owns vectors. These tests pin the PostgreSQL half of that contract,
because every later Stage-5 component (object store, indexer, saga,
reconciliation, the materials API) reads its invariants from here:

* the six entities exist with the fields ``data-model.md`` names, and their
  status columns use *exactly* the vocabularies it declares -- a value the ORM
  accepts but the database CHECK rejects (or the reverse) only shows up in
  production;
* a published ``MaterialVersion`` is immutable: editing creates a new row, so a
  write that mutates a published field must be rejected rather than silently
  overwrite evidence a completed run already cited;
* ``version_number`` is monotonic and unique per material, and only the root
  version may omit ``source_version_id`` -- otherwise the version chain forks
  invisibly;
* ``MaterialVersion`` metadata must equal the ``ObjectVersion`` it points at,
  which is what makes "the bytes are what the row claims" checkable;
* at most one retrieval version per material/document may be ``retrievable``
  and at most one ``EmbeddingVersion`` per knowledge-base cohort may be
  ``active``, enforced by partial unique indexes rather than by convention.

The suite is deliberately store-free: it runs on the ORM metadata, the Python
invariant helpers and the migration module, so it stays fast and gives the same
verdict with or without infrastructure.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlmodel import SQLModel

from backend.app.db import models

ROOT = Path(__file__).resolve().parents[2]

#: ``(vocabulary, table, column)`` -- the Python constant must equal the value
#: set the Stage-5 migration's CHECK constraint accepts.
VOCABULARY_BINDINGS: tuple[tuple[frozenset[str], str, str], ...] = (
    (models.MATERIAL_SOURCE_TYPES, "materials", "source_type"),
    (models.MATERIAL_STATUSES, "materials", "status"),
    (models.MATERIAL_VERSION_STATUSES, "material_versions", "status"),
    (models.OBJECT_SCAN_STATUSES, "object_versions", "scan_status"),
    (models.OBJECT_DELETION_STATES, "object_versions", "deletion_state"),
    (models.EMBEDDING_VERSION_STATUSES, "embedding_versions", "status"),
    (models.VECTOR_DELETION_STATES, "vector_manifests", "deletion_state"),
    (models.RECONCILIATION_STORE_PAIRS, "reconciliation_issues", "store_pair"),
    (models.RECONCILIATION_ISSUE_KINDS, "reconciliation_issues", "issue_kind"),
    (models.RECONCILIATION_SEVERITIES, "reconciliation_issues", "severity"),
    (models.RECONCILIATION_STATES, "reconciliation_issues", "state"),
)

#: The six entities Stage 5 adds, with the table each one declares.
STAGE5_TABLES: tuple[tuple[type[SQLModel], str], ...] = (
    (models.Material, "materials"),
    (models.MaterialVersion, "material_versions"),
    (models.ObjectVersion, "object_versions"),
    (models.EmbeddingVersion, "embedding_versions"),
    (models.VectorManifest, "vector_manifests"),
    (models.ReconciliationIssue, "reconciliation_issues"),
)


def load_migration(revision: str) -> ModuleType:
    """Import a migration by path; its file name is not a Python identifier."""
    matches = sorted((ROOT / "migrations" / "versions").glob(f"{revision}_*.py"))
    assert len(matches) == 1, f"expected exactly one migration for {revision}: {matches}"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stage5() -> ModuleType:
    """The Stage-5 expand migration module (004)."""
    return load_migration("004")


# -- vocabularies ------------------------------------------------------------


def test_vocabularies_are_frozen_and_lowercase() -> None:
    """Vocabularies must be immutable and use one casing convention."""
    for vocabulary, table, column in VOCABULARY_BINDINGS:
        assert isinstance(vocabulary, frozenset), f"{table}.{column} must be a frozenset"
        assert vocabulary, f"{table}.{column} must not be empty"
        for value in vocabulary:
            assert value == value.lower(), f"{table}.{column} value {value!r} is not lowercase"
            assert value.strip() == value and value, f"{table}.{column} value {value!r} is padded"


def test_vocabularies_match_data_model_exactly() -> None:
    """The enums ``data-model.md`` spells out must be reproduced verbatim.

    These are quoted in the specification, so an "improvement" here is a
    specification change and must be made there first.
    """
    assert models.MATERIAL_SOURCE_TYPES == frozenset(
        {"policy", "user_upload", "generated_draft"}
    )
    assert models.MATERIAL_VERSION_STATUSES == frozenset(
        {
            "staging",
            "scanning",
            "indexing",
            "available",
            "quarantined",
            "superseded",
            "deleting",
        }
    )
    assert models.EMBEDDING_VERSION_STATUSES == frozenset({"building", "active", "retired"})
    assert models.RECONCILIATION_ISSUE_KINDS == frozenset(
        {
            "missing_object",
            "orphan_object",
            "missing_vector",
            "orphan_vector",
            "missing_chunk",
            "version_drift",
        }
    )
    # The material lifecycle is the saga in tasks.md T083:
    # pending_upload -> scanning -> indexing -> available -> deleting -> deleted/error
    assert models.MATERIAL_STATUSES == frozenset(
        {
            "pending_upload",
            "scanning",
            "indexing",
            "available",
            "deleting",
            "deleted",
            "error",
        }
    )


def test_python_vocabularies_match_the_database_checks(stage5: ModuleType) -> None:
    """Every Stage-5 vocabulary must be enforced by a CHECK with the same set."""
    declared = {
        (table, column): set(allowed)
        for table, _name, column, allowed in stage5.CHECK_CONSTRAINTS
    }
    for vocabulary, table, column in VOCABULARY_BINDINGS:
        assert (table, column) in declared, (
            f"{table}.{column} has a Python vocabulary but no database CHECK; "
            "an invalid value would be storable"
        )
        assert declared[(table, column)] == set(vocabulary), (
            f"{table}.{column} disagrees: database allows "
            f"{sorted(declared[(table, column)])}, Python allows {sorted(vocabulary)}"
        )


def test_every_database_check_has_a_python_vocabulary(stage5: ModuleType) -> None:
    """The reverse direction, so a constraint cannot drift from its constant."""
    bound = {(table, column) for _vocabulary, table, column in VOCABULARY_BINDINGS}
    for table, name, column, _allowed in stage5.CHECK_CONSTRAINTS:
        assert (table, column) in bound, (
            f"CHECK {name} on {table}.{column} has no Python vocabulary to match it"
        )
        assert name == f"ck_{table}_{column}", f"{name} should be ck_{table}_{column}"


def test_migration_creates_exactly_the_stage5_tables(stage5: ModuleType) -> None:
    """The migration and the ORM must describe the same table set."""
    assert set(stage5.NEW_TABLES) == {table for _model, table in STAGE5_TABLES}
    # Creation order matters: a referenced table must exist first, and the order
    # is also what the downgrade reverses.
    assert stage5.NEW_TABLES.index("materials") < stage5.NEW_TABLES.index("object_versions")
    assert stage5.NEW_TABLES.index("object_versions") < stage5.NEW_TABLES.index(
        "material_versions"
    )
    assert stage5.NEW_TABLES.index("embedding_versions") < stage5.NEW_TABLES.index(
        "vector_manifests"
    )


def test_every_stage5_table_is_tenant_scoped(stage5: ModuleType) -> None:
    """All six entities are tenant-owned, so all six must be RLS-isolated."""
    for model, table in STAGE5_TABLES:
        columns = model.__table__.columns
        assert "tenant_id" in columns, f"{table} must declare tenant ownership"
        assert columns["tenant_id"].nullable is False, f"{table}.tenant_id must be NOT NULL"
        targets = {fk.target_fullname for fk in columns["tenant_id"].foreign_keys}
        assert targets == {"tenants.id"}, f"{table}.tenant_id must reference tenants.id"
    assert set(stage5.STRICT_TENANT_TABLES) == {table for _model, table in STAGE5_TABLES}


def test_every_stage5_table_carries_a_compare_and_set_version() -> None:
    """Stage-5 rows are updated under CAS, never last-write-wins."""
    for model, table in STAGE5_TABLES:
        columns = model.__table__.columns
        assert "version" in columns, f"{table} is CAS-updated but has no version column"
        assert columns["version"].nullable is False, f"{table}.version must be NOT NULL"


# -- fields and relationships ------------------------------------------------


def test_material_declares_lifecycle_and_active_version_pointer() -> None:
    """``Material`` is the logical item: source type, lifecycle, active pointer."""
    columns = models.Material.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "name",
        "source_type",
        "status",
        "active_version_id",
        "read_only",
        "created_at",
        "updated_at",
        "version",
    ):
        assert name in columns, f"materials.{name} is required by data-model.md"
    assert columns["active_version_id"].nullable is True, (
        "a material has no active version until its first version is activated"
    )
    # A formal policy original is read-only; the flag must not be nullable or the
    # "never directly modified" invariant would depend on a tri-state.
    assert columns["read_only"].nullable is False


def test_material_version_identity_and_version_chain() -> None:
    """Version identity, the parent link and the object pointer."""
    table = models.MaterialVersion.__table__
    columns = table.columns
    for name in (
        "id",
        "tenant_id",
        "material_id",
        "version_number",
        "source_version_id",
        "object_version_id",
        "sha256",
        "size_bytes",
        "media_type",
        "status",
        "created_by",
        "created_at",
    ):
        assert name in columns, f"material_versions.{name} is required by data-model.md"

    assert {fk.target_fullname for fk in columns["material_id"].foreign_keys} == {
        "materials.id"
    }
    # The parent link is a self-reference: an edit descends from a version.
    assert {fk.target_fullname for fk in columns["source_version_id"].foreign_keys} == {
        "material_versions.id"
    }
    assert columns["source_version_id"].nullable is True, "the root version has no parent"
    # Required only *after* upload validation, so the column itself is nullable.
    assert {fk.target_fullname for fk in columns["object_version_id"].foreign_keys} == {
        "object_versions.id"
    }
    assert columns["object_version_id"].nullable is True

    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("tenant_id", "material_id", "version_number") in uniques, (
        "version_number must be unique per material, or two rows could claim the "
        f"same version; found {sorted(uniques)}"
    )


def test_version_number_is_monotonic_from_one(stage5: ModuleType) -> None:
    """``version_number`` starts at 1 and only ever increases.

    The bound cannot be proven through the ORM: SQLModel ``table=True`` classes
    skip Pydantic validation on init by design, so ``ge=1`` documents intent
    while the *database* is what actually rejects a bad value. This repository's
    convention (see ``models.py``) is that vocabularies and bounds live next to
    the model and are enforced as CHECK constraints by the migrations, so the
    assertion below is against the migration's predicate, exercised for real in
    :func:`test_row_check_predicates_reject_what_they_must`.
    """
    assert models.MaterialVersion.model_fields["version_number"].metadata, (
        "version_number must declare a lower bound next to the model"
    )
    assert "ck_material_versions_number_positive" in stage5.ROW_CHECK_CONSTRAINTS, (
        "the lower bound must be enforced by the schema, not only documented"
    )

    assert models.next_version_number([]) == 1
    assert models.next_version_number([1, 2, 3]) == 4
    # A gap must not let a later version reuse a smaller number: an evidence row
    # may still cite the larger one.
    assert models.next_version_number([1, 7]) == 8


#: ``(constraint_name, columns, accepted_rows, rejected_rows)`` -- each row is a
#: tuple of values in ``columns`` order.
ROW_CHECK_CASES: tuple[tuple[str, tuple[str, ...], tuple[tuple, ...], tuple[tuple, ...]], ...] = (
    (
        "ck_material_versions_number_positive",
        ("version_number",),
        ((1,), (2,), (99,)),
        ((0,), (-1,)),
    ),
    (
        "ck_material_versions_root_chain",
        ("version_number", "source_version_id"),
        ((1, None), (2, "v1")),
        # A non-root version with no parent forks the chain; a root with a parent
        # is not a root.
        ((2, None), (1, "v1")),
    ),
    (
        "ck_vector_manifests_one_subject",
        ("material_version_id", "document_id"),
        (("mv1", None), (None, "doc1")),
        ((None, None), ("mv1", "doc1")),
    ),
    (
        "ck_vector_manifests_retrievable_complete",
        ("retrievable", "indexed_count", "expected_count"),
        # Not yet retrievable: any progress is fine.
        ((0, 0, 7), (0, 3, 7), (1, 7, 7)),
        # Retrievable but incomplete, or retrievable with nothing indexed at all.
        ((1, 3, 7), (1, 0, 0), (1, 8, 7)),
    ),
)


def test_row_check_predicates_reject_what_they_must(stage5: ModuleType) -> None:
    """Execute each row-level CHECK predicate against SQLite for real.

    Declaring a predicate string is not the same as writing a correct one: a typo
    or an inverted comparison would silently accept the very rows the invariant
    exists to forbid. Each predicate is therefore installed on a scratch table
    and fed rows it must accept and rows it must reject.
    """
    import sqlite3

    declared = set(stage5.ROW_CHECK_CONSTRAINTS)
    covered = {name for name, _columns, _ok, _bad in ROW_CHECK_CASES}
    assert covered == declared, (
        f"every row-level CHECK needs accept/reject cases; missing {sorted(declared - covered)}"
    )

    for name, columns, accepted, rejected in ROW_CHECK_CASES:
        _table, predicate = stage5.ROW_CHECK_CONSTRAINTS[name]
        with sqlite3.connect(":memory:") as conn:
            declaration = ", ".join(f"{column} TEXT" for column in columns)
            conn.execute(f"CREATE TABLE probe ({declaration}, CHECK ({predicate}))")
            placeholders = ", ".join("?" for _ in columns)
            statement = f"INSERT INTO probe ({', '.join(columns)}) VALUES ({placeholders})"
            for row in accepted:
                conn.execute(statement, row)
            for row in rejected:
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute(statement, row)
                    conn.commit()



def test_root_version_rule_is_enforced_in_python_and_in_the_database(
    stage5: ModuleType,
) -> None:
    """Only version 1 may omit ``source_version_id``; later versions must carry it."""
    assert models.material_version_chain_error(version_number=1, source_version_id=None) is None
    assert models.material_version_chain_error(version_number=2, source_version_id="prev") is None
    assert (
        models.material_version_chain_error(version_number=2, source_version_id=None) is not None
    ), "a non-root version without a parent forks the chain invisibly"
    assert (
        models.material_version_chain_error(version_number=1, source_version_id="prev")
        is not None
    ), "the root version cannot descend from another version"
    # The same rule must exist in the schema, not only in the application.
    assert "ck_material_versions_root_chain" in stage5.ROW_CHECK_CONSTRAINTS


def test_object_version_records_provider_identity_and_opaque_key() -> None:
    """``ObjectVersion`` is the only place a bucket/key/VersionId is recorded."""
    table = models.ObjectVersion.__table__
    columns = table.columns
    for name in (
        "id",
        "tenant_id",
        "material_id",
        "material_version_id",
        "bucket_alias",
        "object_key",
        "provider_version_id",
        "sha256",
        "size_bytes",
        "media_type",
        "encryption_algorithm",
        "scan_status",
        "deletion_state",
        "retention_until",
    ):
        assert name in columns, f"object_versions.{name} is required by data-model.md"
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("tenant_id", "bucket_alias", "object_key", "provider_version_id") in uniques, (
        f"one row per provider object version is required; found {sorted(uniques)}"
    )


def test_object_metadata_must_match_the_material_version() -> None:
    """The row's claim about the bytes must equal the object's own metadata."""
    version = models.MaterialVersion(
        tenant_id="t",
        material_id="m",
        version_number=1,
        sha256="a" * 64,
        size_bytes=11,
        media_type="text/plain",
        created_by="u",
    )
    matching = models.ObjectVersion(
        tenant_id="t",
        material_id="m",
        material_version_id=version.id,
        bucket_alias="materials",
        object_key="t/m/1",
        provider_version_id="pv-1",
        sha256="a" * 64,
        size_bytes=11,
        media_type="text/plain",
    )
    assert models.object_metadata_error(version, matching) is None

    for field, value in (
        ("sha256", "b" * 64),
        ("size_bytes", 12),
        ("media_type", "application/pdf"),
    ):
        drifted = matching.model_copy(update={field: value})
        error = models.object_metadata_error(version, drifted)
        assert error is not None and field in error, (
            f"a {field} mismatch between the row and the object must be reported"
        )

    cross_tenant = matching.model_copy(update={"tenant_id": "other"})
    assert models.object_metadata_error(version, cross_tenant) is not None, (
        "a material version must never point at another tenant's object"
    )


def test_published_material_version_fields_are_immutable() -> None:
    """Editing a published version must create a new row, never mutate one."""
    assert models.MATERIAL_VERSION_PUBLISHED_FIELDS >= {
        "tenant_id",
        "material_id",
        "version_number",
        "source_version_id",
        "object_version_id",
        "sha256",
        "size_bytes",
        "media_type",
        "created_by",
        "created_at",
    }
    assert "status" not in models.MATERIAL_VERSION_PUBLISHED_FIELDS, (
        "status is the lifecycle column the saga advances; it cannot be frozen"
    )

    published = models.MaterialVersion(
        tenant_id="t",
        material_id="m",
        version_number=2,
        source_version_id="v1",
        object_version_id="o1",
        sha256="a" * 64,
        size_bytes=11,
        media_type="text/plain",
        created_by="u",
        status="available",
    )
    # A lifecycle transition is allowed...
    assert (
        models.published_field_violations(
            published, published.model_copy(update={"status": "superseded"})
        )
        == []
    )
    # ...but a published field is not.
    assert models.published_field_violations(
        published, published.model_copy(update={"sha256": "b" * 64})
    ) == ["sha256"]
    assert sorted(
        models.published_field_violations(
            published,
            published.model_copy(update={"size_bytes": 12, "media_type": "application/pdf"}),
        )
    ) == ["media_type", "size_bytes"]


def test_embedding_version_describes_the_retrieval_contract() -> None:
    """Provider/model, dimensions, normalization and chunking policy are pinned."""
    columns = models.EmbeddingVersion.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "knowledge_base_id",
        "cohort",
        "provider",
        "model_identifier",
        "dimensions",
        "normalization",
        "chunking_policy_version",
        "status",
        "created_at",
    ):
        assert name in columns, f"embedding_versions.{name} is required by data-model.md"
    assert {fk.target_fullname for fk in columns["knowledge_base_id"].foreign_keys} == {
        "knowledge_bases.id"
    }


def test_exactly_one_active_embedding_version_per_cohort(stage5: ModuleType) -> None:
    """"Exactly one active retrieval version per knowledge base and cohort"."""
    index = stage5.PARTIAL_UNIQUE_INDEXES["uq_embedding_versions_active"]
    assert index["table"] == "embedding_versions"
    assert index["columns"] == ("tenant_id", "knowledge_base_id", "cohort")
    assert index["where"] == "status = 'active'", (
        "the constraint must be partial, or a retired version would block a new one"
    )


def test_vector_manifest_maps_versions_to_milvus(stage5: ModuleType) -> None:
    """The manifest is the PostgreSQL-side record of what exists in Milvus."""
    table = models.VectorManifest.__table__
    columns = table.columns
    for name in (
        "id",
        "tenant_id",
        "knowledge_base_id",
        "material_id",
        "material_version_id",
        "document_id",
        "embedding_version_id",
        "milvus_database",
        "milvus_collection",
        "vector_id_prefix",
        "chunk_ids",
        "expected_count",
        "indexed_count",
        "content_hash",
        "retrievable",
        "activated_at",
        "deletion_state",
    ):
        assert name in columns, f"vector_manifests.{name} is required by data-model.md"
    assert {fk.target_fullname for fk in columns["embedding_version_id"].foreign_keys} == {
        "embedding_versions.id"
    }
    assert columns["retrievable"].nullable is False
    # A manifest describes a material version or a document, never both/neither.
    assert "ck_vector_manifests_one_subject" in stage5.ROW_CHECK_CONSTRAINTS


def test_at_most_one_retrievable_manifest_per_subject(stage5: ModuleType) -> None:
    """"old and new versions are never simultaneously authoritative"."""
    material = stage5.PARTIAL_UNIQUE_INDEXES["uq_vector_manifests_active_material"]
    assert material["table"] == "vector_manifests"
    assert material["columns"] == ("tenant_id", "knowledge_base_id", "material_id")
    assert material["where"] == "retrievable"

    document = stage5.PARTIAL_UNIQUE_INDEXES["uq_vector_manifests_active_document"]
    assert document["columns"] == ("tenant_id", "knowledge_base_id", "document_id")
    assert document["where"] == "retrievable"


def test_reconciliation_issue_records_recovery_state() -> None:
    """A recoverable step records attempts, the next attempt and the last error."""
    table = models.ReconciliationIssue.__table__
    columns = table.columns
    for name in (
        "id",
        "tenant_id",
        "store_pair",
        "issue_kind",
        "resource_kind",
        "resource_id",
        "version_id",
        "observed_fingerprint",
        "expected_fingerprint",
        "severity",
        "state",
        "attempts",
        "next_attempt_at",
        "last_error_code",
        "resolution",
        "detected_at",
        "updated_at",
        "resolved_at",
    ):
        assert name in columns, f"reconciliation_issues.{name} is required by data-model.md"

    # A periodic sweep must re-find the same issue without inserting a new row,
    # so the natural key is unique -- and ``version_id`` is NOT NULL (with an
    # empty-string sentinel) because PostgreSQL treats NULLs as distinct and
    # would let duplicates through.
    assert columns["version_id"].nullable is False, (
        "a nullable version_id makes the natural key non-unique in PostgreSQL"
    )
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("tenant_id", "store_pair", "issue_kind", "resource_kind", "resource_id", "version_id") in uniques, (
        f"the sweep must be idempotent; found {sorted(uniques)}"
    )


def test_reconciliation_terminal_states_are_repair_or_human() -> None:
    """An issue ends either repaired or escalated; it never just disappears."""
    assert models.RECONCILIATION_STATES == frozenset(
        {"open", "repairing", "repaired", "manual_required"}
    )
    assert models.RECONCILIATION_TERMINAL_STATES == frozenset({"repaired", "manual_required"})
    assert models.RECONCILIATION_TERMINAL_STATES < models.RECONCILIATION_STATES


# -- saga vocabulary ---------------------------------------------------------


def test_material_saga_transitions_match_tasks_md() -> None:
    """The saga graph is declared next to the states it moves between."""
    transitions = models.MATERIAL_SAGA_TRANSITIONS
    assert set(transitions) <= models.MATERIAL_STATUSES
    for source, targets in transitions.items():
        assert targets <= models.MATERIAL_STATUSES, f"{source} targets unknown states"
    # tasks.md T083: pending_upload -> scanning -> indexing -> available ->
    # deleting -> deleted/error.
    assert "scanning" in transitions["pending_upload"]
    assert "indexing" in transitions["scanning"]
    assert "available" in transitions["indexing"]
    assert "deleting" in transitions["available"]
    assert transitions["deleting"] >= {"deleted", "error"}
    # ``deleted`` is terminal: a physically deleted material never comes back.
    assert transitions["deleted"] == frozenset()
    # ``error`` must be recoverable, otherwise a transient fault is fatal.
    assert transitions["error"], "error must be able to resume"
