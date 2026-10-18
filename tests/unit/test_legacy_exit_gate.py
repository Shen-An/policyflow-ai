"""T158 gate tests: the ledger must refuse to clear deletion on thin evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.graph.compat import AdapterUsageTelemetry
from backend.app.observability.compatibility import (
    LEGACY_SURFACES,
    CompatibilitySnapshot,
    ReleaseWindow,
    build_snapshot,
)
from backend.app.services.draft_projection import DraftLegacyTelemetry
from backend.app.storage.authority import StorageAuthorityTelemetry, legacy_usage_event

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _elapsed_window() -> ReleaseWindow:
    return ReleaseWindow(start=NOW - timedelta(days=30), end=NOW - timedelta(days=2), label="r38")


def _fresh_snapshot(*, window: ReleaseWindow | None = None, **overrides: object) -> CompatibilitySnapshot:
    kwargs = {
        "storage": StorageAuthorityTelemetry(),
        "adapters": AdapterUsageTelemetry(),
        "drafts": DraftLegacyTelemetry(),
        "browser_shell_requests": 0,
        "pipeline_facade_uses": 0,
        "now": NOW,
    }
    kwargs.update(overrides)
    return build_snapshot(window or _elapsed_window(), **kwargs)  # type: ignore[arg-type]


def test_every_documented_surface_is_measured_or_explicitly_unmeasured() -> None:
    snapshot = _fresh_snapshot(browser_shell_requests=None)
    assert set(snapshot.usage) == set(LEGACY_SURFACES)
    assert snapshot.unmeasured() == ["browser_shell"]
    assert snapshot.zero_use() is False


def test_completed_window_with_zero_use_clears_the_gate() -> None:
    snapshot = _fresh_snapshot()
    assert snapshot.zero_use() is True
    assert snapshot.eligible_for_removal(NOW) is True
    assert snapshot.blockers(NOW) == []


def test_open_window_blocks_even_with_zero_use() -> None:
    window = ReleaseWindow(start=NOW - timedelta(days=14), end=NOW + timedelta(days=1))
    snapshot = _fresh_snapshot(window=window)
    assert snapshot.zero_use() is True
    assert snapshot.eligible_for_removal(NOW) is False
    assert any("尚未结束" in reason for reason in snapshot.blockers(NOW))


def test_any_real_usage_blocks_and_is_reported_with_its_source() -> None:
    storage = StorageAuthorityTelemetry()
    storage.record(
        legacy_usage_event(
            adapter="local_file_write",
            tenant_id="alpha",
            resource_kind="document",
            resource_id="doc-1",
            reason="unit-test",
        )
    )
    snapshot = _fresh_snapshot(storage=storage)
    assert snapshot.usage["local_material_workspace"] == 1
    assert snapshot.eligible_for_removal(NOW) is False
    assert any("local_material_workspace" in reason for reason in snapshot.blockers(NOW))


def test_adapter_and_draft_counters_are_not_collapsed() -> None:
    adapters = AdapterUsageTelemetry()
    drafts = DraftLegacyTelemetry()
    snapshot = _fresh_snapshot(adapters=adapters, drafts=drafts)
    assert snapshot.usage["legacy_chat_adapter"] == 0
    assert snapshot.usage["draft_projection"] == 0

    from backend.app.graph.legacy_adapter import AdapterMode, AdapterTelemetryEvent

    adapters.record(
        AdapterTelemetryEvent(
            adapter="legacy_eval",
            mode=AdapterMode.SHADOW,
            tenant_id="alpha",
            run_id="run-1",
        )
    )
    snapshot = _fresh_snapshot(adapters=adapters, drafts=drafts)
    assert snapshot.usage["legacy_eval_adapter"] == 1
    assert snapshot.usage["legacy_chat_adapter"] == 0


def test_snapshot_rejects_unknown_or_missing_surfaces() -> None:
    window = _elapsed_window()
    usage = {name: 0 for name in LEGACY_SURFACES}
    with pytest.raises(ValueError, match="unknown legacy surfaces"):
        CompatibilitySnapshot(window=window, usage={**usage, "surprise": 0})
    with pytest.raises(ValueError, match="not measured"):
        CompatibilitySnapshot(window=window, usage={"legacy_chat_adapter": 0})


def test_serialised_snapshot_carries_the_reason_it_is_not_eligible() -> None:
    payload = _fresh_snapshot(browser_shell_requests=None).as_dict(NOW)
    assert payload["eligible_for_removal"] is False
    assert payload["zero_use"] is False
    assert payload["window"]["days"] > 0
    assert any("未测量" in reason for reason in payload["blockers"])
