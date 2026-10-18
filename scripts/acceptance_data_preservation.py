"""T149 data-preservation auditor (portable core).

The full migration check compares PostgreSQL against MinIO/Milvus and a legacy
read snapshot.  That needs live infrastructure and a maintenance window, so this
module implements the part that is provable anywhere: row counts, foreign-key and
unique enforcement, and file-hash stability over a real database.

It is deliberately read-only.  A preservation check that can repair what it
measures is not a check.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Tables whose row counts define "nothing was lost" for the core business data.
CORE_TABLES: tuple[str, ...] = (
    "users",
    "knowledge_bases",
    "documents",
    "conversations",
    "messages",
    "memory_items",
    "drafts",
    "runs",
    "run_events",
    "audit_events",
    "material_versions",
    "change_sets",
    "approvals",
)


@dataclass(slots=True)
class PreservationReport:
    database: str
    counts: dict[str, int] = field(default_factory=dict)
    missing_tables: list[str] = field(default_factory=list)
    foreign_key_violations: list[dict[str, Any]] = field(default_factory=list)
    unique_violations: list[dict[str, Any]] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not (
            self.missing_tables or self.foreign_key_violations or self.unique_violations
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "counts": dict(sorted(self.counts.items())),
            "missing_tables": self.missing_tables,
            "foreign_key_violations": self.foreign_key_violations,
            "unique_violations": self.unique_violations,
            "hashes": dict(sorted(self.hashes.items())),
            "notes": list(self.notes),
            "ok": self.ok(),
        }


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def audit_sqlite(path: str | Path, *, files: dict[str, Path] | None = None) -> PreservationReport:
    """Check counts, FK integrity, uniqueness and file hashes on a SQLite copy."""

    db_path = Path(path)
    report = PreservationReport(database=str(db_path))
    if not db_path.exists():
        report.notes.append("database file does not exist")
        report.missing_tables = list(CORE_TABLES)
        return report

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        tables = _table_names(conn)
        for name in CORE_TABLES:
            if name not in tables:
                report.missing_tables.append(name)
                continue
            report.counts[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]

        report.foreign_key_violations = [
            {"table": row[0], "rowid": row[1], "parent": row[2], "fkid": row[3]}
            for row in conn.execute("PRAGMA foreign_key_check").fetchall()
        ]

        report.unique_violations = _unique_violations(conn, tables)
        report.hashes = _hash_files(files or {})
    finally:
        conn.close()
    return report


def _unique_violations(conn: sqlite3.Connection, tables: set[str]) -> list[dict[str, Any]]:
    """Find duplicate values in single-column unique indexes."""

    violations: list[dict[str, Any]] = []
    for table in sorted(tables):
        for index in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
            # PRAGMA index_list: (seq, name, unique, origin, partial)
            if not index[2]:
                continue
            columns = [
                row[2]
                for row in conn.execute(f'PRAGMA index_info("{index[1]}")').fetchall()
            ]
            if len(columns) != 1 or not columns[0]:
                continue
            column = columns[0]
            duplicate = conn.execute(
                f'SELECT "{column}", COUNT(*) c FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL GROUP BY "{column}" HAVING c > 1 LIMIT 5'
            ).fetchall()
            for value, count in duplicate:
                violations.append({"table": table, "column": column, "value": value, "count": count})
    return violations


def _hash_files(files: dict[str, Path]) -> dict[str, str]:
    """SHA-256 per named file; a missing file is reported by the auditor, not skipped."""

    result: dict[str, str] = {}
    for label, path in sorted(files.items()):
        if not path.exists():
            result[label] = "MISSING"
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        result[label] = digest.hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="T149 portable data-preservation audit")
    parser.add_argument("database", help="path to the SQLite database to inspect")
    parser.add_argument("--out", default="artifacts/acceptance/data-preservation.json")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="file whose SHA-256 must be stable (repeatable)",
    )
    args = parser.parse_args(argv)

    files: dict[str, Path] = {}
    for spec in args.file:
        label, _, raw = spec.partition("=")
        if not raw:
            parser.error(f"--file expects LABEL=PATH, got {spec!r}")
        files[label] = Path(raw)

    report = audit_sqlite(args.database, files=files)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "ok": report.ok(), "tables": len(report.counts)}, ensure_ascii=False))
    return 0 if report.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
