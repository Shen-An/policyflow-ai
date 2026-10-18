"""T148 [US5] reproduce the administration evidence in ``artifacts/admin/us5``.

The evidence has to be generated, not hand-written: it is a matrix of *actual*
HTTP responses from a fully assembled application over the two-tenant fixture, a
seeded-fault detection report, and the honest boundary of what this phase does
not prove.

Run from the repository root::

    python scripts/collect_us5_evidence.py

The script writes four files next to this one:

* ``rbac-matrix.json`` / ``rbac-matrix.md`` -- principal x endpoint status codes;
* ``seeded-faults.json`` -- findings, health aggregates and redaction checks;
* ``summary.json`` -- everything above plus the environment and a verdict list.

It uses SQLite so it runs on any host. The same suites also run against
PostgreSQL when ``POLICYFLOW_TEST_DATABASE_URL`` is exported (see
``tests/conftest.py``); this script does not claim otherwise.
"""

from __future__ import annotations

import asyncio
import json
import platform
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from tests.support_admin import (  # noqa: E402
    ADMIN,
    API_KEY_VALUE,
    BETA_ADMIN,
    EMPLOYEE,
    ISSUE_ALPHA,
    ISSUE_BETA,
    ISSUE_OPEN,
    PLATFORM,
    ROLE_ADMIN,
    RUN_ALPHA,
    RUN_BETA,
    TENANT_ALPHA,
    TENANT_BETA,
    _seed,
    headers,
)

OUT_DIR = ROOT / "artifacts" / "admin" / "us5"
SECRET = "stage9-admin-secret"


def _settings(url: str, tmp_path: Path) -> Settings:
    return Settings(
        DATABASE_URL=url,
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY=SECRET,
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        BOOTSTRAP_ADMIN_PASSWORD="pw",
        _env_file=None,
    )


def _code(resp: Any) -> str:
    """Return the stable error code from the envelope, or the success marker."""
    if resp.status_code < 400:
        return "ok"
    try:
        return str(resp.json()["error"]["code"])
    except Exception:  # noqa: BLE001 - evidence must never hide a failure
        return "unparsed-error-body"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="us5-evidence-"))
    url = f"sqlite:///{(tmp_path / 'evidence.db').as_posix()}"
    asyncio.run(_seed(url))

    admin = headers(TENANT_ALPHA, ADMIN, tmp_path)
    employee = headers(TENANT_ALPHA, EMPLOYEE, tmp_path)
    platform_operator = headers(TENANT_ALPHA, PLATFORM, tmp_path)
    beta_admin = headers(TENANT_BETA, BETA_ADMIN, tmp_path)

    principals = {
        "alpha_admin": admin,
        "alpha_employee": employee,
        "alpha_platform(cross_tenant)": platform_operator,
        "beta_admin": beta_admin,
    }
    reads = [
        ("GET", "/api/v2/admin/roles", None),
        ("GET", "/api/v2/admin/grants", None),
        ("GET", "/api/v2/admin/quota", None),
        ("GET", "/api/v2/admin/health", None),
        ("GET", "/api/v2/admin/runs", None),
        ("GET", "/api/v2/admin/reconciliation", None),
        ("GET", "/api/v2/admin/capacity", None),
        ("GET", f"/api/v2/admin/runs/{RUN_ALPHA}/audit", None),
        ("GET", f"/api/v2/admin/runs/{RUN_BETA}/audit", None),
    ]

    matrix: list[dict[str, Any]] = []
    with TestClient(create_app(_settings(url, tmp_path))) as client:
        for who, head in principals.items():
            for method, path, body in reads:
                resp = client.request(method, path, headers=head, json=body)
                matrix.append(
                    {
                        "principal": who,
                        "tenant": TENANT_BETA if who == "beta_admin" else TENANT_ALPHA,
                        "method": method,
                        "path": path,
                        "status": resp.status_code,
                        "code": _code(resp),
                    }
                )

        # -- seeded cross-store faults -------------------------------------
        findings = client.get(
            "/api/v2/admin/reconciliation", headers=admin
        ).json()
        health = client.get("/api/v2/admin/health", headers=admin).json()
        finding_ids = {item["issue_id"] for item in findings["items"]}

        # -- audit timeline: correlated, redacted --------------------------
        timeline = client.get(
            f"/api/v2/admin/runs/{RUN_ALPHA}/audit", headers=admin
        ).json()
        serialized_timeline = json.dumps(timeline)
        redacted_metadata = [
            item["metadata"]
            for item in timeline["items"]
            if item.get("redactions")
        ]

        # -- failed-job recovery -------------------------------------------
        recover_first = client.post(
            f"/api/v2/admin/runs/{RUN_ALPHA}/recover", headers=admin, json={}
        )
        recover_second = client.post(
            f"/api/v2/admin/runs/{RUN_ALPHA}/recover", headers=admin, json={}
        )

        # -- quota view is tenant-scoped -----------------------------------
        quota = client.get("/api/v2/admin/quota", headers=admin).json()

        # -- fresh grant / fresh revocation ---------------------------------
        grant = client.post(
            "/api/v2/admin/grants",
            headers=admin,
            json={"user_id": EMPLOYEE, "role_id": ROLE_ADMIN},
        )
        employee_after_grant = client.get(
            "/api/v2/admin/roles", headers=employee
        )
        grant_id = grant.json()["grant"]["grant_id"]
        revocation = client.post(
            f"/api/v2/admin/grants/{grant_id}/revoke",
            headers=admin,
            json={"reason": "us5 evidence: prove revocation is immediate"},
        )
        employee_after_revoke = client.get(
            "/api/v2/admin/roles", headers=employee
        )

        # -- human handling of a seeded finding -----------------------------
        retry = client.post(
            f"/api/v2/admin/reconciliation/{ISSUE_ALPHA}/retry",
            headers=admin,
            json={"reason": "us5 evidence"},
        )
        resolve = client.post(
            f"/api/v2/admin/reconciliation/{ISSUE_OPEN}/resolve",
            headers=admin,
            json={"resolution": "us5 evidence: re-uploaded the missing chunk"},
        )

    def _row(principal: str, path: str) -> int:
        return next(
            item["status"]
            for item in matrix
            if item["principal"] == principal and item["path"] == path
        )

    checks = {
        "employee_denied_every_read": all(
            item["status"] == 403
            for item in matrix
            if item["principal"] == "alpha_employee"
        ),
        "foreign_run_timeline_is_404": _row("alpha_admin", f"/api/v2/admin/runs/{RUN_BETA}/audit")
        == 404,
        "beta_admin_cannot_read_alpha_run": _row(
            "beta_admin", f"/api/v2/admin/runs/{RUN_ALPHA}/audit"
        )
        == 404,
        "capacity_requires_cross_tenant": _row("alpha_admin", "/api/v2/admin/capacity") == 403
        and _row("alpha_platform(cross_tenant)", "/api/v2/admin/capacity") == 200,
        "findings_are_tenant_scoped": finding_ids == {ISSUE_ALPHA, ISSUE_OPEN},
        "fingerprints_do_not_leak_storage_keys": "object_key" not in json.dumps(findings).lower(),
        "timeline_redacts_api_key": "sk-live-should-never-leak" not in serialized_timeline
        and API_KEY_VALUE not in serialized_timeline,
        # The fixture stores `C:\\Users\\secret\\policy.pdf`; JSON re-escapes the
        # backslashes, so assert on both the file name and the directory segment
        # rather than on one literal spelling.
        "timeline_redacts_host_path": "policy.pdf" not in serialized_timeline.lower()
        and "users\\\\secret" not in serialized_timeline.lower(),
        "timeline_merges_audit_and_run_events": {
            item["source"] for item in timeline["items"]
        }
        == {"audit", "run_event"},
        "recovery_transitions_recoverable_only": recover_first.status_code == 200
        and recover_first.json()["state"] == "queued",
        "second_recovery_is_conflict": recover_second.status_code == 409
        and recover_second.json()["error"]["code"] == "RUN_NOT_RECOVERABLE",
        "quota_hides_other_tenant": "qp-beta"
        not in {policy["policy_id"] for policy in quota["policies"]},
        "quota_keeps_global_fallback": "qp-global"
        in {policy["policy_id"] for policy in quota["policies"]},
        "health_reports_open_findings": bool(health["reconciliation"]["open_issues"]),
        "grant_takes_effect_without_relogin": employee_after_grant.status_code == 200,
        "revocation_takes_effect_immediately": employee_after_revoke.status_code == 403,
        "retry_resets_attempt_budget": retry.status_code == 200
        and retry.json()["state"] == "open",
        "resolve_closes_finding": resolve.status_code == 200
        and resolve.json()["state"] == "resolved",
    }

    generated_at = datetime.now(UTC).isoformat()
    environment = {
        "generated_at": generated_at,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "database": "sqlite (file) -- PostgreSQL is covered by the pytest suites",
        "tenants": {"alpha": TENANT_ALPHA, "beta": TENANT_BETA},
        "principals": list(principals),
        "fixture_issues": {
            "alpha_manual": ISSUE_ALPHA,
            "alpha_open": ISSUE_OPEN,
            "beta_manual": ISSUE_BETA,
        },
    }

    (OUT_DIR / "rbac-matrix.json").write_text(
        json.dumps({"environment": environment, "rows": matrix}, indent=2),
        encoding="utf-8",
    )

    md = [
        "# US5 管理员治理 — 双租户 RBAC 矩阵",
        "",
        f"生成时间：{generated_at}",
        "",
        "同一份应用（SQLite 双租户 fixture，见 `tests/support_admin.py`），"
        "每个单元格都是实际 HTTP 状态码；括号内是稳定错误码。",
        "",
        "| 主体 | " + " | ".join(f"`{path}`" for _, path, _ in reads) + " |",
        "| --- | " + " | ".join("---" for _ in reads) + " |",
    ]
    for who in principals:
        cells = []
        for _, path, _ in reads:
            row = next(item for item in matrix if item["principal"] == who and item["path"] == path)
            cells.append(f"{row['status']} ({row['code']})")
        md.append(f"| {who} | " + " | ".join(cells) + " |")
    md += [
        "",
        "说明：",
        "",
        "- `alpha_employee` 没有 `admin` 动作，所有读取都是 403，且拒绝动作会先写审计再返回。",
        "- `beta_admin` 管理自己的租户；跨租户的 run 一律 404，不暴露“存在但无权”。",
        "- `alpha_platform` 持有独立的 `cross_tenant_admin`，只有它能读 `/capacity`。",
        "- 文件路径里用 `` ` `` 包裹的是请求路径，不是可枚举的资源。",
        "",
    ]
    (OUT_DIR / "rbac-matrix.md").write_text("\n".join(md), encoding="utf-8")

    faults = {
        "environment": environment,
        "findings": findings,
        "health": health,
        "timeline": {
            "entries": len(timeline["items"]),
            "sources": sorted({item["source"] for item in timeline["items"]}),
            "redacted_entries": len(redacted_metadata),
            "metadata_after_redaction": redacted_metadata,
        },
        "recovery": {
            "first": recover_first.json(),
            "second": {"status": recover_second.status_code, "body": recover_second.json()},
        },
        "grant_lifecycle": {
            "created": grant.status_code == 201,
            "after_grant": employee_after_grant.status_code,
            "revoked": revocation.status_code,
            "after_revoke": employee_after_revoke.status_code,
        },
        "handling": {"retry": retry.json(), "resolve": resolve.json()},
        "checks": checks,
    }
    (OUT_DIR / "seeded-faults.json").write_text(
        json.dumps(faults, indent=2), encoding="utf-8"
    )

    summary = {
        "environment": environment,
        "checks": checks,
        "passed": all(checks.values()),
        "failed": sorted(name for name, ok in checks.items() if not ok),
        "artifacts": ["rbac-matrix.json", "rbac-matrix.md", "seeded-faults.json"],
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
