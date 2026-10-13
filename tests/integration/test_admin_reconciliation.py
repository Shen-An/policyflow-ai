"""T141 [US5] reconciliation governance: seeded findings, manual handling, worker audit.

The reconciler already detects drift (Stage 5); this suite covers what governance
adds on top:

* the admin list is tenant-scoped and exposes fingerprints only;
* a human-escalated issue can be retried with a fresh budget or closed with a
  resolution, and a terminal issue refuses both;
* the scheduled worker audits every finding transition, and its deterministic
  event ids make a repeated pass over unchanged findings append nothing (so
  "detected" does not become a pile of duplicate rows);
* an unreachable store is recorded as ``blocked`` rather than as thousands of
  false findings.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.db.models import AuditEvent
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import build_async_engine
from backend.app.jobs.reconciliation_worker import ReconciliationWorker
from backend.app.storage.reconciliation import ReconciliationUnavailable, SweepReport
from tests.support_admin import (
    ADMIN,
    EMPLOYEE,
    ISSUE_ALPHA,
    ISSUE_BETA,
    ISSUE_OPEN,
    TENANT_ALPHA,
    TENANT_BETA,
    admin_client,
    bearer,
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with admin_client(tmp_path) as assembled:
        yield assembled


def _headers(client: TestClient, who: str = ADMIN) -> dict[str, str]:
    return bearer(client, TENANT_ALPHA, who)


def _factory(client: TestClient) -> async_sessionmaker:
    url = str(client.app.state.settings.DATABASE_URL)
    return async_sessionmaker(build_async_engine(url, None), expire_on_commit=False)


# -- admin listing -----------------------------------------------------------


def test_findings_are_tenant_scoped_and_expose_no_storage_keys(client: TestClient) -> None:
    resp = client.get("/api/v2/admin/reconciliation", headers=_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["issue_id"] for item in body["items"]}
    assert ids == {ISSUE_ALPHA, ISSUE_OPEN}
    assert body["total"] == 2

    serialized = resp.text.lower()
    for leak in ("object_key", "bucket", "host_path", "c:\\\\", "/work/"):
        assert leak not in serialized, f"the finding view leaks {leak!r}"

    item = next(entry for entry in body["items"] if entry["issue_id"] == ISSUE_ALPHA)
    assert item["issue_kind"] == "missing_object"
    assert item["severity"] == "critical"
    assert item["state"] == "manual_required"
    assert item["attempts"] == 5 and item["max_attempts"] == 5


def test_filter_by_state_and_kind(client: TestClient) -> None:
    opened = client.get(
        "/api/v2/admin/reconciliation?state=open", headers=_headers(client)
    )
    assert opened.status_code == 200
    assert {item["issue_id"] for item in opened.json()["items"]} == {ISSUE_OPEN}

    by_kind = client.get(
        "/api/v2/admin/reconciliation?issue_kind=missing_object", headers=_headers(client)
    )
    assert by_kind.status_code == 200
    assert {item["issue_id"] for item in by_kind.json()["items"]} == {ISSUE_ALPHA}


def test_member_cannot_list_findings(client: TestClient) -> None:
    resp = client.get("/api/v2/admin/reconciliation", headers=_headers(client, EMPLOYEE))
    assert resp.status_code == 403, resp.text


# -- manual handling ---------------------------------------------------------


def test_retry_resets_the_attempt_budget_and_is_audited(client: TestClient) -> None:
    resp = client.post(
        f"/api/v2/admin/reconciliation/{ISSUE_ALPHA}/retry",
        headers=_headers(client),
        json={"reason": "provider recovered"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "issue_id": ISSUE_ALPHA,
        "state": "open",
        "previous_state": "manual_required",
    }

    listed = client.get("/api/v2/admin/reconciliation", headers=_headers(client)).json()
    item = next(entry for entry in listed["items"] if entry["issue_id"] == ISSUE_ALPHA)
    assert item["state"] == "open"
    assert item["attempts"] == 0
    assert item["last_error_code"] is None
    assert item["version"] == 2

    audit = client.get(
        f"/api/v2/admin/runs/{ISSUE_ALPHA}/audit", headers=_headers(client)
    )
    # Not a run, so the run timeline must not pretend to know it.
    assert audit.status_code == 404


def test_resolve_closes_the_finding_and_a_second_close_is_a_conflict(
    client: TestClient,
) -> None:
    first = client.post(
        f"/api/v2/admin/reconciliation/{ISSUE_ALPHA}/resolve",
        headers=_headers(client),
        json={"resolution": "re-uploaded the missing policy PDF"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "resolved"

    second = client.post(
        f"/api/v2/admin/reconciliation/{ISSUE_ALPHA}/resolve",
        headers=_headers(client),
        json={"resolution": "again"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "RECONCILIATION_ISSUE_NOT_ACTIONABLE"

    retried = client.post(
        f"/api/v2/admin/reconciliation/{ISSUE_ALPHA}/retry",
        headers=_headers(client),
        json={},
    )
    assert retried.status_code == 409, retried.text


def test_foreign_finding_actions_are_a_404(client: TestClient) -> None:
    for action, payload in (
        ("retry", {}),
        ("resolve", {"resolution": "done"}),
    ):
        resp = client.post(
            f"/api/v2/admin/reconciliation/{ISSUE_BETA}/{action}",
            headers=_headers(client),
            json=payload,
        )
        assert resp.status_code == 404, resp.text
        assert TENANT_BETA not in resp.text


# -- scheduled worker --------------------------------------------------------


class _FakeReconciler:
    """Records the sweep calls and replays one canned report."""

    def __init__(self, report: SweepReport | None = None, error: Exception | None = None):
        self.report = report
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def sweep(self, *, tenant_id: str, repair: bool = False) -> SweepReport:
        self.calls.append((tenant_id, repair))
        if self.error is not None:
            raise self.error
        assert self.report is not None
        return self.report


def _worker(client: TestClient, reconciler) -> ReconciliationWorker:
    return ReconciliationWorker(
        factory=_factory(client),
        reconciler_factory=lambda: reconciler,
        interval_seconds=60,
        repair=False,
    )


def _audit_rows(client: TestClient, action: str) -> list[tuple[str, str]]:
    async def read() -> list[tuple[str, str]]:
        factory = _factory(client)
        async with UnitOfWork(factory=factory) as uow:
            await uow.set_tenant_context(TENANT_ALPHA)
            rows = (
                await uow.session.execute(
                    select(AuditEvent.outcome).where(
                        AuditEvent.tenant_id == TENANT_ALPHA,
                        AuditEvent.action == action,
                    )
                )
            ).scalars().all()
            return [(action, str(outcome)) for outcome in rows]

    return asyncio.run(read())


def test_worker_audits_transitions_idempotently(client: TestClient) -> None:
    report = SweepReport(
        tenant_id=TENANT_ALPHA,
        checked_versions=4,
        checked_manifests=2,
        opened=[f"missing_chunk:{ISSUE_OPEN}"],
        escalated=[f"missing_object:{ISSUE_ALPHA}"],
    )
    reconciler = _FakeReconciler(report=report)
    worker = _worker(client, reconciler)

    first = asyncio.run(worker.run_once([TENANT_ALPHA]))
    # ``findings`` counts newly opened/reopened issues; the escalated one is a
    # transition (and is audited) but was already a finding.
    assert first.findings == 1
    assert len(first.reports[0].escalated) == 1
    assert first.audited_transitions == 2
    assert first.audited_summaries == 1
    assert reconciler.calls == [(TENANT_ALPHA, False)]

    assert _audit_rows(client, "reconciliation.issue.detected") == [
        ("reconciliation.issue.detected", "allowed")
    ]
    assert _audit_rows(client, "reconciliation.issue.escalated") == [
        ("reconciliation.issue.escalated", "blocked")
    ]

    # A second identical pass re-finds the same unchanged issues. Because the
    # event ids are deterministic on (issue, state, attempts), nothing new is
    # appended -- "detected" is a stable fact, not a counter.
    second = asyncio.run(worker.run_once([TENANT_ALPHA]))
    assert second.audited_transitions == 0
    assert second.audited_summaries == 0
    assert len(_audit_rows(client, "reconciliation.issue.detected")) == 1


def test_worker_records_an_unreachable_store_as_blocked(client: TestClient) -> None:
    reconciler = _FakeReconciler(error=ReconciliationUnavailable("milvus down"))
    cycle = asyncio.run(_worker(client, reconciler).run_once([TENANT_ALPHA]))

    assert cycle.unavailable_tenants == [TENANT_ALPHA]
    assert cycle.reports == []
    assert cycle.findings == 0
    assert _audit_rows(client, "reconciliation.sweep.unavailable") == [
        ("reconciliation.sweep.unavailable", "blocked")
    ]


def test_worker_passes_repair_through_only_when_enabled(client: TestClient) -> None:
    report = SweepReport(tenant_id=TENANT_ALPHA)
    reconciler = _FakeReconciler(report=report)
    worker = ReconciliationWorker(
        factory=_factory(client),
        reconciler_factory=lambda: reconciler,
        interval_seconds=60,
        repair=True,
    )
    asyncio.run(worker.run_once([TENANT_ALPHA]))
    assert reconciler.calls == [(TENANT_ALPHA, True)]


def test_worker_enumerates_active_tenants(client: TestClient) -> None:
    worker = _worker(client, _FakeReconciler(report=SweepReport(tenant_id="x")))
    tenants = asyncio.run(worker.active_tenants())
    # ``initialize_database`` also creates the ``legacy`` bootstrap tenant, so the
    # enumeration is asserted as a superset rather than an exact pair.
    assert {TENANT_ALPHA, TENANT_BETA} <= set(tenants)
    assert len(tenants) == 3


def test_audit_rows_are_bounded_per_transition(client: TestClient) -> None:
    """The audit table grows with *transitions*, not with sweep frequency."""

    report = SweepReport(tenant_id=TENANT_ALPHA, opened=[f"missing_chunk:{ISSUE_OPEN}"])
    worker = _worker(client, _FakeReconciler(report=report))
    for _ in range(3):
        asyncio.run(worker.run_once([TENANT_ALPHA]))

    async def count() -> int:
        factory = _factory(client)
        async with UnitOfWork(factory=factory) as uow:
            await uow.set_tenant_context(TENANT_ALPHA)
            return int(
                (
                    await uow.session.execute(
                        select(func.count(AuditEvent.id)).where(
                            AuditEvent.tenant_id == TENANT_ALPHA,
                            AuditEvent.action == "reconciliation.issue.detected",
                        )
                    )
                ).scalar_one()
            )

    assert asyncio.run(count()) == 1
