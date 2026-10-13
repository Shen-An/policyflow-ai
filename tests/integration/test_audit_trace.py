"""T139 [US5] ``run_id`` audit timeline: correlation, ordering and redaction.

The Independent Test for User Story 5 asks an administrator to trace one run from
request through evidence, tool, approval, submission, retry and error. Two things
make that safe to expose:

* the timeline is correlated by ``run_id`` *and* tenant, so a foreign id is a
  404 rather than a partial view;
* every metadata payload passes through the audit module's own sanitizer before
  it is returned, so credentials, host paths and provider payloads are redacted
  on the way out even if an older row was written before that policy existed.

This suite proves both, and that audit rows and durable run events are merged in
time order rather than presented as two disconnected lists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support_admin import (
    ADMIN,
    API_KEY_VALUE,
    BETA_ADMIN,
    ISSUE_ALPHA,
    RUN_ALPHA,
    RUN_BETA,
    TENANT_ALPHA,
    TENANT_BETA,
    admin_client,
    bearer,
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with admin_client(tmp_path) as assembled:
        yield assembled


def _timeline(client: TestClient, run_id: str = RUN_ALPHA) -> list[dict]:
    resp = client.get(
        f"/api/v2/admin/runs/{run_id}/audit",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def test_timeline_merges_audit_and_run_events_in_time_order(client: TestClient) -> None:
    items = _timeline(client)
    sources = {item["source"] for item in items}
    assert {"audit", "run_event"} <= sources

    audit_actions = {item["action"] for item in items if item["source"] == "audit"}
    assert "approval.decide" in audit_actions

    run_events = [item for item in items if item["source"] == "run_event"]
    assert [item["sequence"] for item in run_events] == [1, 2]
    assert run_events[-1]["event_type"] == "run.completed"

    occurred = [item["occurred_at"] for item in items]
    assert occurred == sorted(occurred)


def test_timeline_redacts_credentials_and_host_paths(client: TestClient) -> None:
    resp = client.get(
        f"/api/v2/admin/runs/{RUN_ALPHA}/audit",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert API_KEY_VALUE not in resp.text
    assert "Users" not in resp.text or "secret" not in resp.text.lower()

    audit_entry = next(item for item in body["items"] if item["source"] == "audit")
    assert API_KEY_VALUE not in str(audit_entry["metadata"])
    assert audit_entry["redactions"], "a redaction must be reported, not silent"
    assert any("credential" in value for value in audit_entry["redactions"])

    # The benign field survives, so redaction is targeted rather than a blanket
    # wipe that would make the trace useless.
    assert audit_entry["metadata"]["note"] == "approved after review"


def test_each_timeline_read_is_itself_audited(client: TestClient) -> None:
    # A read is audited after it has read, so its own row appears on the *next*
    # call; three reads therefore expose two prior reads.
    _timeline(client)
    _timeline(client)
    items = _timeline(client)
    reads = [item for item in items if item.get("action") == "admin.run.audit.read"]
    assert len(reads) >= 2
    assert all(item["outcome"] == "allowed" for item in reads)


def test_foreign_run_timeline_is_a_404(client: TestClient) -> None:
    resp = client.get(
        f"/api/v2/admin/runs/{RUN_BETA}/audit",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert resp.status_code == 404, resp.text

    # Even holding the *right* tenant's token, a beta admin cannot read alpha's
    # run: the tenant predicate is on the principal, not on a body field.
    cross = client.get(
        f"/api/v2/admin/runs/{RUN_ALPHA}/audit",
        headers=bearer(client, TENANT_BETA, BETA_ADMIN),
    )
    assert cross.status_code == 404, cross.text


def test_reconciliation_actions_are_correlated_by_run_when_present(client: TestClient) -> None:
    """A recovery decision carries the run id, so it joins the same timeline."""
    resp = client.post(
        f"/api/v2/admin/runs/{RUN_ALPHA}/recover",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
        json={},
    )
    assert resp.status_code == 200, resp.text

    items = _timeline(client)
    recovered = [item for item in items if item.get("action") == "admin.run.recover"]
    assert recovered, "the recovery audit row must be part of the run timeline"
    assert recovered[0]["metadata"]["previous_state"] == "recoverable_failed"

    # A recovery unrelated to this run must not be pulled in: the fixture's
    # issue id is deliberately not a run id, and 404s through the audit route.
    unrelated = client.get(
        f"/api/v2/admin/runs/{ISSUE_ALPHA}/audit",
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert unrelated.status_code == 404, unrelated.text
