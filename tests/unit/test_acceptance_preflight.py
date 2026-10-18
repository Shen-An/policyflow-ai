"""Tests for the read-only acceptance preflights (T149 parity, T152 consumers)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, relative: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules, so the module
    # must be visible there before it executes.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


parity = _load("acceptance_legacy_read_compare", "scripts/acceptance_legacy_read_compare.py")
consumers = _load("t152_legacy_column_consumers", "scripts/t152_legacy_column_consumers.py")


def test_projection_matches_the_legacy_draft_read_shape(tmp_path) -> None:
    db = tmp_path / "parity.sqlite"
    parity.seed(db)
    report = parity.compare(db)
    assert report.ok is True
    assert len(report.rows) == len(parity.FIXTURE)
    assert all(row["mismatches"] == [] for row in report.rows)


def test_parity_detects_a_diverged_legacy_status(tmp_path) -> None:
    db = tmp_path / "parity.sqlite"
    parity.seed(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE drafts SET status = 'confirmed' WHERE id = 'cs-ready'")
        conn.commit()

    report = parity.compare(db)
    assert report.ok is False
    row = next(entry for entry in report.rows if entry["id"] == "cs-ready")
    assert row["mismatches"][0]["field"] == "status"
    assert row["mismatches"][0]["legacy"] == "confirmed"
    assert row["mismatches"][0]["projected"] == "draft"


def test_parity_flags_a_draft_without_a_change_set(tmp_path) -> None:
    db = tmp_path / "parity.sqlite"
    parity.seed(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO drafts VALUES ('orphan','file_change_set','t','','draft')"
        )
        conn.commit()

    report = parity.compare(db)
    assert report.ok is False
    orphan = next(entry for entry in report.rows if entry["id"] == "orphan")
    assert orphan["mismatches"][0]["reason"] == "no change set"


def test_consumer_scan_finds_draft_readers(tmp_path) -> None:
    module = tmp_path / "backend" / "app" / "api" / "routes_x.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from backend.app.db.models import Draft\n"
        "def read(draft: Draft):\n"
        "    return draft.related_sources\n",
        encoding="utf-8",
    )

    found = consumers.scan(tmp_path)
    # Import, annotation and attribute access are all legacy model references.
    assert [entry["line"] for entry in found] == [1, 2, 3]


def test_consumer_scan_is_clean_when_nothing_reads_the_model(tmp_path) -> None:
    module = tmp_path / "backend" / "app" / "api" / "routes_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("def read(x):\n    return x.content\n", encoding="utf-8")

    assert consumers.scan(tmp_path) == []


def test_consumer_scan_skips_tests_and_migrations(tmp_path) -> None:
    for relative in ("tests/test_x.py", "alembic/versions/001_x.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from backend.app.db.models import Draft\n", encoding="utf-8")

    assert consumers.scan(tmp_path) == []
