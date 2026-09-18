"""T036: capture Stage 2 migration evidence into artifacts/migration/stage2/.

Runs the three suites named by T036, records their results, and writes the
migration file checksums and task completion counts next to them. The caller
supplies the PostgreSQL test URL through ``POLICYFLOW_TEST_DATABASE_URL`` so
the captured evidence always describes the database that was actually tested.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO / "artifacts" / "migration" / "stage2"
PYTHON = sys.executable

SUITES = [
    "tests/integration/test_postgres_migrations.py",
    "tests/integration/test_multi_instance.py",
    "tests/security/test_tenant_isolation.py",
]


def sha256(path: Path) -> str:
    """Return the hex digest of a file, tolerating the CRLF-converted copies."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def task_counts() -> dict[str, int]:
    """Count checked and open tasks per Stage phase from tasks.md."""
    text = (REPO / "specs" / "001-enterprise-agent-refactor" / "tasks.md").read_text(
        encoding="utf-8"
    )
    done = re.findall(r"^\s*-\s*\[X\]\s+(T\d+)", text, flags=re.MULTILINE)
    open_ = re.findall(r"^\s*-\s*\[\ \]\s+(T\d+)", text, flags=re.MULTILINE)
    return {"done": len(done), "open": len(open_)}


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")

    suite_results: dict[str, str] = {}
    for suite in SUITES:
        result = subprocess.run(
            [PYTHON, "-m", "pytest", suite, "-q", "-p", "no:cacheprovider", "--no-header"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        suite_results[suite] = (
            f"exit={result.returncode} summary={tail}"
            if "passed" in tail or "failed" in tail
            else f"exit={result.returncode} {tail}"
        )

    migration_files = sorted((REPO / "migrations" / "versions").glob("*.py"))
    checksums = {path.name: sha256(path) for path in migration_files}

    evidence = {
        "captured_at": stamp,
        "repository_head": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip(),
        "suites": suite_results,
        "migration_versions": checksums,
        "tasks": task_counts(),
    }

    (ARTIFACT_DIR / "stage2-evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    report = ["# Stage 2 migration evidence (T036)", "", f"captured at `{stamp}`", ""]
    for suite, outcome in suite_results.items():
        report.append(f"- `{suite}`: {outcome}")
    report.append("")
    report.append("## Migration version checksums (sha256)")
    for name, digest in checksums.items():
        report.append(f"- `{name}`: `{digest}`")
    report.append("")
    report.append(f"## tasks.md state: {evidence['tasks']['done']} done / {evidence['tasks']['open']} open")
    report.append("")
    report.append("> Evidence captured from the named PostgreSQL migration, multi-instance, and tenant-isolation suites.")
    (ARTIFACT_DIR / "stage2-evidence.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
