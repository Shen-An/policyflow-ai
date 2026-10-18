"""T149 portable preservation audit: counts, FK, uniqueness, file hashes.

The PostgreSQL/MinIO/Milvus comparison needs a live environment and a maintenance
window; until then this suite pins the auditor itself, so a future cutover has a
tool whose failure modes are already known.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.acceptance_data_preservation import CORE_TABLES, audit_sqlite


def _seed(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, tenant_id TEXT);
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            status TEXT
        );
        INSERT INTO users (id, email, tenant_id) VALUES (1, 'a@example.com', 'alpha');
        INSERT INTO runs (id, user_id, status) VALUES (10, 1, 'succeeded');
        """
    )
    present = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for name in CORE_TABLES:
        if name not in present:
            conn.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()


def test_clean_database_reports_counts_and_no_violations(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    _seed(db)
    blob = tmp_path / "policy.pdf"
    blob.write_bytes(b"%PDF-1.7 example bytes")

    report = audit_sqlite(db, files={"policy": blob})

    assert report.ok() is True
    assert report.counts["users"] == 1
    assert report.counts["runs"] == 1
    assert report.foreign_key_violations == []
    assert report.unique_violations == []
    assert len(report.hashes["policy"]) == 64


def test_unique_constraint_is_enforced_by_the_database(tmp_path: Path) -> None:
    """Duplicates cannot be created, so preservation relies on the constraint itself.

    The auditor's duplicate scan stays as a safety net for schemas where the index
    was added late; here we pin the stronger guarantee: the write is refused.
    """

    db = tmp_path / "dup.db"
    _seed(db)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users (id, email, tenant_id) VALUES (2, 'a@example.com', 'alpha')")
    conn.close()

    report = audit_sqlite(db)
    assert report.ok() is True
    assert report.counts["users"] == 1


def test_orphaned_foreign_key_is_reported(tmp_path: Path) -> None:
    db = tmp_path / "orphan.db"
    _seed(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO runs (id, user_id, status) VALUES (11, 999, 'queued')")
    conn.commit()
    conn.close()

    report = audit_sqlite(db)
    assert report.ok() is False
    assert report.foreign_key_violations


def test_missing_core_tables_are_named_not_ignored(tmp_path: Path) -> None:
    db = tmp_path / "partial.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
    conn.commit()
    conn.close()

    report = audit_sqlite(db)
    missing = set(report.missing_tables)
    assert {"conversations", "messages", "material_versions"} <= missing
    assert set(CORE_TABLES) - missing == set(report.counts) == {"users"}
    assert report.ok() is False


def test_missing_file_is_marked_not_skipped(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    _seed(db)
    report = audit_sqlite(db, files={"gone": tmp_path / "nope.bin"})
    assert report.hashes["gone"] == "MISSING"


def test_absent_database_is_a_failed_report_not_an_exception(tmp_path: Path) -> None:
    report = audit_sqlite(tmp_path / "absent.db")
    assert report.ok() is False
    assert report.notes == ["database file does not exist"]


@pytest.mark.parametrize("table", ["users", "runs"])
def test_counts_are_sorted_and_serialisable(tmp_path: Path, table: str) -> None:
    db = tmp_path / "clean.db"
    _seed(db)
    payload = audit_sqlite(db).as_dict()
    assert payload["ok"] is True
    assert list(payload["counts"]) == sorted(payload["counts"])
    assert payload["counts"][table] == 1
