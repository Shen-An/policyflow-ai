"""Static contracts for the Stage 9 acceptance load profile (T153)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.load import locustfile
from tests.load.acceptance_profile import (
    ACCEPTANCE_TARGETS,
    CONCURRENCY,
    WORKFLOW_WEIGHTS,
    AcceptanceProfile,
    ConcurrencyTracker,
    evaluate_acceptance,
    weighted_workflow,
)
from tests.load.locustfile import PROFILE_CONFIGS, profile_options, summarize_run

GOOD = {
    "active_sessions": 1_010.0,
    "sse_streams": 1_000.0,
    "non_llm_rps": 250.0,
    "non_llm_p95_ms": 420.0,
    "first_stage_p95_ms": 800.0,
    "server_failure_rate": 0.002,
    "sse_cleanup_seconds": 12.0,
    "soak_minutes": 30.0,
    "rss_growth_ratio": 1.05,
}


def test_acceptance_profile_matches_spec_shape() -> None:
    assert "acceptance" in PROFILE_CONFIGS
    options = profile_options({"POLICYFLOW_LOAD_PROFILE": "acceptance"})
    assert options["users"] == 2_200
    assert options["workflow"] == "acceptance"
    assert options["requires_dedicated_environment"] is True
    assert options["workflow_weights"]["chat"] == 1_000.0
    assert options["workflow_weights"]["sse"] == 1_000.0
    assert options["workflow_weights"]["file"] > 0
    assert options["workflow_weights"]["isolation"] > 0


def test_thresholds_come_from_the_spec_not_from_the_run() -> None:
    targets = ACCEPTANCE_TARGETS.as_dict()
    assert targets["active_sessions"] == 1_000
    assert targets["sse_streams"] == 1_000
    assert targets["non_llm_rps"] == 200
    assert targets["non_llm_p95_ms"] == 500.0
    assert targets["first_stage_p95_ms"] == 1_000.0
    assert targets["sse_cleanup_seconds"] == 30.0
    assert targets["soak_minutes"] == 30
    assert targets["max_server_failure_rate"] == 0.01


def test_evaluation_passes_only_when_every_target_is_met() -> None:
    result = evaluate_acceptance(GOOD)
    assert result["verdict"] == "pass"
    assert result["missing"] == []
    assert all(check["verdict"] == "pass" for check in result["checks"].values())


def test_concurrency_is_a_real_gate() -> None:
    """Latency/throughput numbers alone must not pass the acceptance profile.

    Regression for the review finding: a summary with clean latency, p95,
    failure rate, cleanup, soak and RSS numbers used to pass even though the run
    proved nothing about how many sessions or SSE streams were actually held.
    """

    no_concurrency = {
        key: value
        for key, value in GOOD.items()
        if key not in {"active_sessions", "sse_streams"}
    }
    inconclusive = evaluate_acceptance(no_concurrency)
    assert inconclusive["verdict"] == "inconclusive"
    assert "active_sessions" in inconclusive["missing"]
    assert "sse_streams" in inconclusive["missing"]

    too_few_sessions = evaluate_acceptance({**GOOD, "active_sessions": 400.0})
    assert too_few_sessions["verdict"] == "fail"
    assert too_few_sessions["checks"]["active_sessions"]["verdict"] == "fail"

    too_few_streams = evaluate_acceptance({**GOOD, "sse_streams": 10.0})
    assert too_few_streams["verdict"] == "fail"
    assert too_few_streams["checks"]["sse_streams"]["verdict"] == "fail"


def test_latency_and_error_regressions_fail() -> None:
    slow = evaluate_acceptance({**GOOD, "non_llm_p95_ms": 900.0})
    assert slow["verdict"] == "fail"
    assert slow["checks"]["non_llm_latency"]["verdict"] == "fail"

    errors = evaluate_acceptance({**GOOD, "server_failure_rate": 0.05})
    assert errors["verdict"] == "fail"
    assert errors["checks"]["server_failures"]["verdict"] == "fail"


def test_missing_metrics_are_inconclusive_not_pass() -> None:
    partial = {k: v for k, v in GOOD.items() if k != "sse_cleanup_seconds"}
    result = evaluate_acceptance(partial)
    assert result["verdict"] == "inconclusive"
    assert "sse_cleanup_seconds" in result["missing"]
    assert "sse_cleanup" not in result["checks"]


def test_short_soak_and_leak_fail() -> None:
    short = evaluate_acceptance({**GOOD, "soak_minutes": 5.0})
    assert short["verdict"] == "fail"
    assert short["checks"]["soak_duration"]["verdict"] == "fail"

    leaky = evaluate_acceptance({**GOOD, "rss_growth_ratio": 1.8})
    assert leaky["verdict"] == "fail"
    assert leaky["checks"]["soak_leak_guard"]["verdict"] == "fail"


@pytest.mark.parametrize("raw", ["not-a-number", None, {}])
def test_unparseable_metrics_are_inconclusive(raw: object) -> None:
    result = evaluate_acceptance({**GOOD, "non_llm_rps": raw})
    assert result["verdict"] == "inconclusive"
    assert "non_llm_rps" in result["missing"]


def test_profile_dataclass_is_the_documented_one() -> None:
    profile = AcceptanceProfile()
    assert profile.users == 2_200
    assert profile.workflow_weights == WORKFLOW_WEIGHTS
    assert profile.options()["targets"]["soak_minutes"] == 30


def test_workflow_weights_are_actually_drawn() -> None:
    """The acceptance mix must dispatch to every workflow, not just `chat`."""

    import random

    rng = random.Random(20260913)
    draws = [weighted_workflow(WORKFLOW_WEIGHTS, rng) for _ in range(4_000)]
    counts = {name: draws.count(name) for name in WORKFLOW_WEIGHTS}
    assert set(counts) == {"chat", "sse", "file", "isolation"}
    assert all(count > 0 for count in counts.values())
    # chat and sse carry equal weight; file/isolation are the 100-vs-100 tail.
    assert abs(counts["chat"] - counts["sse"]) < 300
    assert counts["chat"] > 5 * counts["file"]


def test_acceptance_user_dispatches_by_weight_and_others_by_profile() -> None:
    user = object.__new__(locustfile.CapacityUser)
    user._rng = __import__("random").Random(7)

    user.profile = PROFILE_CONFIGS["acceptance"]
    seen = {user._workflow() for _ in range(500)}
    assert seen == {"chat", "sse", "file", "isolation"}

    user.profile = PROFILE_CONFIGS["sse"]
    assert user._workflow() == "sse"
    user.profile = PROFILE_CONFIGS["load"]
    assert user._workflow() == "chat"


def test_concurrency_tracker_records_peak_and_completions() -> None:
    tracker = ConcurrencyTracker()
    with tracker.track("sse"):
        with tracker.track("sse"):
            assert tracker.current("sse") == 2
        assert tracker.current("sse") == 1
    assert tracker.peak("sse") == 2
    assert tracker.completed("sse") == 2
    assert tracker.snapshot()["sse_streams"] == 2
    assert tracker.snapshot()["active_sessions"] == 0


def test_tenant_selection_spreads_users_across_tenants() -> None:
    from tests.load.seed_data import build_seed_data

    tenants = build_seed_data(seed=20260913)["tenants"]
    picked = {locustfile.tenant_for_slot(tenants, slot)["id"] for slot in range(4)}
    assert len(picked) == len(tenants)
    foreign = locustfile.foreign_tenant(tenants, tenants[0])
    assert foreign is not None and foreign["id"] != tenants[0]["id"]


def test_login_uses_the_seeded_tenant_username() -> None:
    from tests.load.seed_data import build_seed_data

    tenant = build_seed_data(seed=20260913)["tenants"][1]
    assert locustfile.load_username(tenant) == f"{tenant['code']}-employee"


def test_summarize_run_derives_metrics_from_the_raw_log(tmp_path: Path) -> None:
    """The run summary must come from recorded requests, not operator numbers."""

    rows = [
        {
            "timestamp": "2026-10-21T00:00:00+00:00",
            "request_type": "POST",
            "name": "GET /api/knowledge-bases",
            "status_code": "200",
            "response_time_ms": "100.0",
            "response_length": "10",
            "exception": "",
            "context": "{}",
        },
        {
            "timestamp": "2026-10-21T00:00:01+00:00",
            "request_type": "POST",
            "name": "POST /api/chat/stream",
            "status_code": "200",
            "response_time_ms": "900.0",
            "response_length": "10",
            "exception": "",
            "context": json.dumps({"sse_first_event_ms": 120.0}),
        },
        {
            "timestamp": "2026-10-21T00:00:02+00:00",
            "request_type": "GET",
            "name": "GET /api/knowledge-bases/{id} (cross-tenant)",
            "status_code": "500",
            "response_time_ms": "50.0",
            "response_length": "0",
            "exception": "",
            "context": json.dumps({"isolation_violation": True}),
        },
    ]
    with (tmp_path / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (tmp_path / "resource.jsonl").write_text(
        json.dumps({"rss_bytes": 100}) + "\n" + json.dumps({"rss_bytes": 130}) + "\n",
        encoding="utf-8",
    )

    summary = summarize_run(tmp_path, duration_seconds=60.0)

    assert summary["request_count"] == 3
    assert summary["server_failure_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert summary["non_llm_rps"] == pytest.approx(2 / 60.0, abs=1e-3)
    assert summary["non_llm_p95_ms"] == pytest.approx(100.0, abs=1e-6)
    assert summary["first_stage_p95_ms"] == pytest.approx(120.0, abs=1e-6)
    assert summary["rss_growth_ratio"] == pytest.approx(1.3, abs=1e-4)
    assert summary["isolation_violations"] == 1
    # No concurrency was observed, so the verdict must be inconclusive, not pass.
    assert summary["active_sessions"] == 0
    assert summary["verdict"] == "fail"


def test_tracker_snapshot_is_the_shape_the_summary_reads() -> None:
    CONCURRENCY.reset()
    with CONCURRENCY.track("chat"):
        snapshot = CONCURRENCY.snapshot()
        assert snapshot["active_sessions"] == 1
    assert CONCURRENCY.snapshot()["active_sessions"] == 1
    assert CONCURRENCY.snapshot()["completed_chat_sessions"] == 1
    CONCURRENCY.reset()
