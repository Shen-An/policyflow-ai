"""T089 [US1] contract tests for the Stage-6 workspace/change/approval model.

Stage 6 is the business MVP: select material -> evidence-backed draft -> diff ->
human approval -> fresh RBAC + digest recheck -> idempotent mock submit, with the
formal policy original never mutated. Six entities carry that, and this suite pins
the PostgreSQL contract every later Stage-6 component reads from:

* the entities exist with the fields ``data-model.md`` names, and status columns
  use exactly its vocabularies -- a value the ORM accepts but the DB CHECK rejects
  (or the reverse) only surfaces in production;
* ownership is immutable and tenant-scoped, and every mutable aggregate carries a
  ``version`` for compare-and-set;
* the two state machines (ApprovalRequest, SubmissionJob) are declared next to the
  model and match ``data-model.md`` exactly, including that ``pending`` is the only
  state an approval may be decided from and ``unknown_outcome`` must reconcile
  before it can retry;
* a ChangeSetItem's path is unique within its workspace (the escape surface T090
  guards starts here) and a non-root operation needs a source version;
* ``SubmissionJob`` carries the ``(tenant_id, connector_id, idempotency_key)``
  unique key that makes "at most one accepted business action" a database fact,
  not a hope.

Store-free by design: it runs on ORM metadata, the Python invariant helpers and
the migration module, so it gives the same verdict with or without infrastructure.
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

VOCABULARY_BINDINGS: tuple[tuple[frozenset[str], str, str], ...] = (
    (models.WORKSPACE_STATUSES, "task_workspaces", "status"),
    (models.WORKSPACE_INPUT_PURPOSES, "workspace_inputs", "purpose"),
    (models.CHANGE_SET_STATES, "change_sets", "state"),
    (models.CHANGE_SET_SIDE_EFFECT_CLASSES, "change_sets", "side_effect_class"),
    (models.CHANGE_ITEM_OPERATIONS, "change_set_items", "operation"),
    (models.APPROVAL_STATUSES, "approval_requests", "status"),
    (models.SUBMISSION_STATES, "submission_jobs", "state"),
)

STAGE6_TABLES: tuple[tuple[type[SQLModel], str], ...] = (
    (models.TaskWorkspace, "task_workspaces"),
    (models.WorkspaceInput, "workspace_inputs"),
    (models.ChangeSet, "change_sets"),
    (models.ChangeSetItem, "change_set_items"),
    (models.ApprovalRequest, "approval_requests"),
    (models.SubmissionJob, "submission_jobs"),
)


def load_migration(revision: str) -> ModuleType:
    matches = sorted((ROOT / "migrations" / "versions").glob(f"{revision}_*.py"))
    assert len(matches) == 1, f"expected exactly one migration for {revision}: {matches}"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stage6() -> ModuleType:
    """The Stage-6 expand migration module (005)."""
    return load_migration("005")


# -- vocabularies ------------------------------------------------------------


def test_vocabularies_are_frozen_and_lowercase() -> None:
    for vocabulary, table, column in VOCABULARY_BINDINGS:
        assert isinstance(vocabulary, frozenset), f"{table}.{column} must be a frozenset"
        assert vocabulary, f"{table}.{column} must not be empty"
        for value in vocabulary:
            assert value == value.lower(), f"{table}.{column} value {value!r} not lowercase"
            assert value.strip() == value and value, f"{table}.{column} value {value!r} padded"


def test_vocabularies_match_data_model_exactly() -> None:
    """The enums ``data-model.md`` spells out, reproduced verbatim."""
    assert models.WORKSPACE_STATUSES == frozenset(
        {
            "provisioning",
            "ready",
            "processing",
            "changes_ready",
            "awaiting_approval",
            "closed",
            "expired",
            "failed",
        }
    )
    assert models.CHANGE_SET_STATES == frozenset(
        {
            "draft",
            "ready",
            "awaiting_approval",
            "approved",
            "rejected",
            "invalidated",
            "applied",
        }
    )
    assert models.APPROVAL_STATUSES == frozenset(
        {"pending", "approved", "rejected", "expired", "invalidated", "consumed"}
    )
    assert models.SUBMISSION_STATES == frozenset(
        {
            "ready",
            "executing",
            "succeeded",
            "recoverable_failed",
            "cancelled",
            "terminal_failed",
            "unknown_outcome",
            "reconciling",
        }
    )


def test_python_vocabularies_match_the_database_checks(stage6: ModuleType) -> None:
    declared = {
        (table, column): set(allowed)
        for table, _name, column, allowed in stage6.CHECK_CONSTRAINTS
    }
    for vocabulary, table, column in VOCABULARY_BINDINGS:
        assert (table, column) in declared, (
            f"{table}.{column} has a Python vocabulary but no database CHECK"
        )
        assert declared[(table, column)] == set(vocabulary), (
            f"{table}.{column} disagrees: DB {sorted(declared[(table, column)])} vs "
            f"Python {sorted(vocabulary)}"
        )


def test_every_database_check_has_a_python_vocabulary(stage6: ModuleType) -> None:
    bound = {(table, column) for _vocabulary, table, column in VOCABULARY_BINDINGS}
    for table, name, column, _allowed in stage6.CHECK_CONSTRAINTS:
        assert (table, column) in bound, f"CHECK {name} on {table}.{column} has no vocabulary"
        assert name == f"ck_{table}_{column}", f"{name} should be ck_{table}_{column}"


def test_migration_creates_exactly_the_stage6_tables(stage6: ModuleType) -> None:
    assert set(stage6.NEW_TABLES) == {table for _model, table in STAGE6_TABLES}
    # Creation order: a referenced table exists first.
    assert stage6.NEW_TABLES.index("task_workspaces") < stage6.NEW_TABLES.index(
        "workspace_inputs"
    )
    assert stage6.NEW_TABLES.index("change_sets") < stage6.NEW_TABLES.index(
        "change_set_items"
    )
    assert stage6.NEW_TABLES.index("change_sets") < stage6.NEW_TABLES.index(
        "approval_requests"
    )
    assert stage6.NEW_TABLES.index("approval_requests") < stage6.NEW_TABLES.index(
        "submission_jobs"
    )


def test_every_stage6_table_is_tenant_scoped_and_cas() -> None:
    for model, table in STAGE6_TABLES:
        columns = model.__table__.columns
        assert "tenant_id" in columns, f"{table} must declare tenant ownership"
        assert columns["tenant_id"].nullable is False, f"{table}.tenant_id must be NOT NULL"
        assert {fk.target_fullname for fk in columns["tenant_id"].foreign_keys} == {
            "tenants.id"
        }
        assert "version" in columns, f"{table} is CAS-updated but has no version"
        assert columns["version"].nullable is False


# -- fields and relationships ------------------------------------------------


def test_task_workspace_binds_ownership_and_hides_infrastructure() -> None:
    columns = models.TaskWorkspace.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "run_id",
        "user_id",
        "session_id",
        "status",
        "sandbox_job_ref",
        "input_manifest_digest",
        "policy_snapshot",
        "expires_at",
        "created_at",
        "closed_at",
    ):
        assert name in columns, f"task_workspaces.{name} is required by data-model.md"
    # sandbox_job_ref is an opaque reference, never a host path -- so it is a plain
    # bounded string, and the API layer (T107) must never surface it.
    assert columns["sandbox_job_ref"].nullable is True


def test_workspace_input_selects_one_material_version() -> None:
    columns = models.WorkspaceInput.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "workspace_id",
        "material_version_id",
        "purpose",
        "staged_hash",
        "read_only",
    ):
        assert name in columns, f"workspace_inputs.{name} is required by data-model.md"
    assert {fk.target_fullname for fk in columns["material_version_id"].foreign_keys} == {
        "material_versions.id"
    }
    assert models.WORKSPACE_INPUT_PURPOSES == frozenset({"read", "edit"})


def test_change_set_records_the_digests_approval_binds_to() -> None:
    columns = models.ChangeSet.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "workspace_id",
        "run_id",
        "source_manifest_digest",
        "evidence_set_digest",
        "summary",
        "side_effect_class",
        "state",
    ):
        assert name in columns, f"change_sets.{name} is required by data-model.md"


def test_change_set_item_path_is_unique_per_change_set() -> None:
    table = models.ChangeSetItem.__table__
    columns = table.columns
    for name in (
        "id",
        "tenant_id",
        "change_set_id",
        "source_version_id",
        "proposed_version_id",
        "operation",
        "normalized_path",
        "before_hash",
        "after_hash",
        "diff_artifact_ref",
    ):
        assert name in columns, f"change_set_items.{name} is required by data-model.md"
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("change_set_id", "normalized_path") in uniques, (
        "a path must be unique within its change set, or two items could target "
        f"the same file; found {sorted(uniques)}"
    )


def test_change_item_operation_rule_is_enforced() -> None:
    """A non-create operation needs a source version; create must not have one."""
    assert models.CHANGE_ITEM_OPERATIONS == frozenset({"create", "update", "delete"})
    assert (
        models.change_item_source_error(operation="create", source_version_id=None) is None
    )
    assert (
        models.change_item_source_error(operation="update", source_version_id="v1") is None
    )
    assert (
        models.change_item_source_error(operation="update", source_version_id=None)
        is not None
    ), "an update with no source version has nothing to diff against"
    assert (
        models.change_item_source_error(operation="create", source_version_id="v1")
        is not None
    ), "a create cannot descend from an existing version"


def test_approval_request_binds_one_review_target() -> None:
    columns = models.ApprovalRequest.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "run_id",
        "change_set_id",
        "action",
        "destination",
        "action_digest",
        "requested_by",
        "decided_by",
        "authorization_version",
        "status",
        "expires_at",
        "decided_at",
        "reason",
    ):
        assert name in columns, f"approval_requests.{name} is required by data-model.md"
    # The digest is 64-char lowercase hex; the column must hold exactly that.
    assert columns["action_digest"].type.length >= 64


def test_submission_job_has_the_at_most_once_key() -> None:
    table = models.SubmissionJob.__table__
    columns = table.columns
    for name in (
        "id",
        "tenant_id",
        "run_id",
        "approval_id",
        "connector_id",
        "destination",
        "idempotency_key",
        "expected_target_version",
        "state",
        "attempts",
        "provider_receipt",
        "sanitized_result",
        "next_attempt_at",
    ):
        assert name in columns, f"submission_jobs.{name} is required by data-model.md"
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("tenant_id", "connector_id", "idempotency_key") in uniques, (
        "the at-most-one-accepted-action guarantee must be a unique constraint; "
        f"found {sorted(uniques)}"
    )


# -- state machines ----------------------------------------------------------


def test_approval_state_machine_matches_data_model() -> None:
    transitions = models.APPROVAL_TRANSITIONS
    assert set(transitions) <= models.APPROVAL_STATUSES
    for source, targets in transitions.items():
        assert targets <= models.APPROVAL_STATUSES, f"{source} targets unknown states"
    # pending -> approved|rejected|expired|invalidated
    assert transitions["pending"] == frozenset(
        {"approved", "rejected", "expired", "invalidated"}
    )
    # approved -> consumed|invalidated|expired
    assert transitions["approved"] == frozenset({"consumed", "invalidated", "expired"})
    # terminal states have no outgoing transition
    for terminal in ("rejected", "expired", "invalidated", "consumed"):
        assert transitions[terminal] == frozenset(), f"{terminal} must be terminal"


def test_submission_state_machine_requires_reconcile_before_retry() -> None:
    transitions = models.SUBMISSION_TRANSITIONS
    assert set(transitions) <= models.SUBMISSION_STATES
    assert transitions["ready"] >= {"executing"}
    assert transitions["executing"] >= {"succeeded", "recoverable_failed"}
    assert transitions["recoverable_failed"] == frozenset({"ready"})
    # An unknown provider outcome must reconcile before it can retry: there is no
    # direct unknown_outcome -> ready edge, only through reconciling.
    assert "ready" not in transitions["unknown_outcome"]
    assert transitions["unknown_outcome"] == frozenset({"reconciling"})
    assert transitions["reconciling"] == frozenset(
        {"succeeded", "ready", "terminal_failed"}
    )
    for terminal in ("succeeded", "cancelled", "terminal_failed"):
        assert transitions[terminal] == frozenset(), f"{terminal} must be terminal"


def test_approval_invalidation_inputs_are_declared() -> None:
    """Any change to these inputs invalidates a prior approval (data-model.md).

    They are declared as a set so the digest (T101) and the approval service
    (T102) agree on exactly what the approval is bound to.
    """
    assert models.APPROVAL_DIGEST_INPUTS >= {
        "action",
        "destination",
        "source_versions",
        "output_versions",
        "file_hashes",
        "diff",
        "evidence_set",
        "permission_snapshot",
        "side_effects",
    }
