"""T140 [US5] tenant quota, final usage, health and pagination.

The governance dashboard must answer "what are we allowed, what did we use, and
what is stuck" from authoritative rows, without ever folding another tenant's
numbers in. The fixture deliberately gives beta much larger numbers (999 requests,
9999 tokens, its own live lease) so a leak would be obvious rather than subtle.

Pagination is asserted on real pages, including the empty page past the end: a
dashboard that silently clamps ``offset`` to the last page is worse than one that
returns nothing, because it misreports which records exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support_admin import (
    ADMIN,
    EMPLOYEE,
    RUN_ALPHA,
    RUN_ALPHA_2,
    RUN_ALPHA_3,
    TENANT_ALPHA,
    admin_client,
    bearer,
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with admin_client(tmp_path) as assembled:
        yield assembled


def _get(client: TestClient, path: str, who: str = ADMIN) -> dict:
    resp = client.get(path, headers=bearer(client, TENANT_ALPHA, who))
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- quota and usage ---------------------------------------------------------


def test_quota_returns_the_highest_active_policy_per_scope(client: TestClient) -> None:
    body = _get(client, "/api/v2/admin/quota")
    policies = {policy["policy_id"]: policy for policy in body["policies"]}

    # The tenant's version 2 wins over version 1; the global fallback is kept;
    # beta's policy is never visible.
    assert "qp-alpha-v2" in policies
    assert "qp-alpha-v1" not in policies
    assert "qp-global" in policies
    assert "qp-beta" not in policies
    assert policies["qp-alpha-v2"]["requests_per_window"] == 100


def test_usage_is_final_actual_and_tenant_scoped(client: TestClient) -> None:
    body = _get(client, "/api/v2/admin/quota")
    assert body["usage"] == [
        {
            "kind": "chat",
            "records": 1,
            "requests": 3,
            "reserved_tokens": 200,
            "actual_tokens": 120,
        }
    ]
    # Only alpha's live lease is counted (beta also holds one).
    assert body["live_leases"] == 1


# -- health ------------------------------------------------------------------


def test_health_reports_queue_indexing_and_submission_state(client: TestClient) -> None:
    body = _get(client, "/api/v2/admin/health")

    job_states = {item["value"]: item["count"] for item in body["jobs"]}
    assert job_states == {
        "queued": 1,
        "recoverable_failed": 1,
        "terminal_failed": 1,
    }
    assert body["oldest_queued_at"] is not None

    assert body["retrieval"] == {
        "retrievable_manifests": 0,
        "manifests_with_count_drift": 0,
        "active_materials": 0,
    }
    open_issues = {
        (item["issue_kind"], item["severity"], item["count"])
        for item in body["reconciliation"]["open_issues"]
    }
    assert open_issues == {
        ("missing_object", "critical", 1),
        ("missing_chunk", "critical", 1),
    }

    # Beta owns no jobs in this fixture; had the predicate been missing, beta's
    # rows would still be zero here, so the stronger assertion is on quota/usage
    # above. This one guards against the shape drifting silently.
    assert body["documents"] == []
    assert body["submissions"] == []
    assert body["workspaces"] == []


# -- pagination --------------------------------------------------------------


def test_runs_paginate_with_a_stable_total(client: TestClient) -> None:
    first = _get(client, "/api/v2/admin/runs?limit=1&offset=0")
    second = _get(client, "/api/v2/admin/runs?limit=1&offset=1")
    third = _get(client, "/api/v2/admin/runs?limit=1&offset=2")
    past_end = _get(client, "/api/v2/admin/runs?limit=1&offset=99")

    assert first["total"] == second["total"] == third["total"] == 3
    assert first["limit"] == 1 and first["offset"] == 0
    ids = [first["items"][0]["run_id"], second["items"][0]["run_id"],
           third["items"][0]["run_id"]]
    assert ids == [RUN_ALPHA_3, RUN_ALPHA_2, RUN_ALPHA]  # newest first
    assert past_end["items"] == []
    assert past_end["total"] == 3


def test_runs_filter_by_status_and_kind(client: TestClient) -> None:
    all_runs = _get(client, "/api/v2/admin/runs")
    assert all_runs["total"] == 3

    match = _get(client, "/api/v2/admin/runs?status=succeeded&kind=chat")
    assert match["total"] == 3

    none = _get(client, "/api/v2/admin/runs?status=terminal_failed")
    assert none["total"] == 0
    assert none["items"] == []


def test_grants_paginate_and_filter_by_member(client: TestClient) -> None:
    page = _get(client, "/api/v2/admin/grants?limit=1&offset=2")
    assert page["total"] == 3
    assert len(page["items"]) == 1

    mine = _get(client, f"/api/v2/admin/grants?member_id={EMPLOYEE}")
    assert mine["total"] == 1
    assert mine["items"][0]["user_id"] == EMPLOYEE


def test_pagination_bounds_are_rejected_at_the_edge(client: TestClient) -> None:
    too_large = client.get(
        "/api/v2/admin/runs?limit=101",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    negative = client.get(
        "/api/v2/admin/runs?offset=-1",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert too_large.status_code == 422, too_large.text
    assert negative.status_code == 422, negative.text
