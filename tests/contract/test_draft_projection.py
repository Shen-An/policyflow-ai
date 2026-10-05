"""T108 [US1] the Draft legacy projection and its Stage 9 removal gate.

Pure unit tests. The Draft row is a migration adapter now; these pin the two
honest-boundary properties: every legacy write is counted (so the removal gate is
real), and a ChangeSet can be read through the old shape without a second
authority writing a Draft row -- the projection is derived and read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.app.services.draft_projection import (
    DraftLegacyTelemetry,
    legacy_draft_write,
    project_change_set,
)


def test_telemetry_starts_clear_and_counts_writes() -> None:
    telemetry = DraftLegacyTelemetry()
    assert telemetry.zero_use_over_window() is True
    telemetry.record(
        legacy_draft_write(
            operation="create", draft_id="d1", tenant_id="t", reason="legacy write"
        )
    )
    assert telemetry.zero_use_over_window() is False
    assert telemetry.usage_count("create") == 1
    assert telemetry.total_usage() == 1


def test_an_unknown_operation_cannot_be_counted() -> None:
    with pytest.raises(ValueError, match="unknown legacy draft operation"):
        legacy_draft_write(operation="frobnicate", draft_id="d1", tenant_id="t", reason="x")


def test_telemetry_records_no_draft_content() -> None:
    telemetry = DraftLegacyTelemetry()
    telemetry.record(
        legacy_draft_write(
            operation="create", draft_id="d1", tenant_id="t", reason="legacy write"
        )
    )
    event = telemetry.events[0]
    assert set(vars(event)) == {"operation", "draft_id", "tenant_id", "reason"}, (
        "a new field risks carrying draft content into the ledger"
    )


@dataclass
class _ChangeSet:
    id: str
    state: str
    summary: str


@dataclass
class _Item:
    normalized_path: str
    operation: str


@pytest.mark.parametrize(
    "state,expected",
    [
        ("draft", "draft"),
        ("awaiting_approval", "draft"),
        ("approved", "confirmed"),
        ("applied", "confirmed"),
        ("rejected", "discarded"),
        ("invalidated", "discarded"),
    ],
)
def test_change_set_projects_into_the_legacy_read_shape(state: str, expected: str) -> None:
    projection = project_change_set(
        _ChangeSet(id="cs-1", state=state, summary="raise the per-diem cap"),
        [_Item(normalized_path="forms/reimbursement.txt", operation="update")],
    )
    assert projection["id"] == "cs-1"
    assert projection["draft_type"] == "file_change_set"
    assert projection["status"] == expected
    assert projection["projection"] is True, "the view must mark itself derived"
    assert projection["related_sources"][0]["path"] == "forms/reimbursement.txt"
    # The projection carries no object key, host path or sandbox reference.
    text = str(projection).lower()
    for leak in ("object_key", "sandbox", "/work/", "bucket"):
        assert leak not in text
