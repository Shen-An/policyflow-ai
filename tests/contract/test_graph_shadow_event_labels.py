"""Parity diff labels: stage events must be comparable without inventing divergence."""

from __future__ import annotations

from backend.app.graph.shadow import _event_label


def test_stage_label_keeps_stage_and_status() -> None:
    label = _event_label({"event": "stage", "stage": "RouterAgent", "status": "running"})

    assert label == "stage:RouterAgent:running"


def test_bare_stage_event_degrades_to_plain_name() -> None:
    # A stub pipeline may emit ``{"event": "stage"}`` with no metadata. Labelling that as
    # "stage::" would look different from a real "stage:RouterAgent:running" event and
    # report a divergence that says nothing about the shadow run.
    assert _event_label({"event": "stage"}) == "stage"
    assert _event_label({"event": "stage", "stage": "", "status": None}) == "stage"


def test_non_stage_events_keep_their_own_name() -> None:
    assert _event_label({"event": "diagnostics_partial"}) == "diagnostics_partial"
    assert _event_label({"event": "plan_step", "step": 2}) == "plan_step"


def test_stage_and_plain_stage_are_distinguishable() -> None:
    assert _event_label({"event": "stage"}) != _event_label(
        {"event": "stage", "stage": "AnswerAgent", "status": "success"}
    )
