"""T149: compare the legacy Draft read shape with the ChangeSet projection.

This runs on a seeded SQLite fixture, not on the production PostgreSQL copy: it
proves the two *read paths* agree field-by-field for the same logical row, which
is the part that can be verified without a cluster.  A production comparison
still needs a restore point and the real data — see ``artifacts/acceptance/``.

Exit code 0 only when every fixture row agrees on every compared field.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from backend.app.services.draft_projection import project_change_set

OUT = Path("artifacts/acceptance/legacy-read-parity.json")

#: The projection may only expose these Draft read fields (plus ``projection``).
COMPARED_FIELDS = ("draft_type", "status", "title", "related_sources")

#: Fixture rows: legacy Draft row <-> equivalent ChangeSet + items.
FIXTURE = [
    {
        "id": "cs-approved",
        "summary": "Update the reimbursement cap",
        "state": "approved",
        "legacy_status": "confirmed",
        "items": [("policies/reimburse.md", "update")],
    },
    {
        "id": "cs-rejected",
        "summary": "Delete the old travel form",
        "state": "rejected",
        "legacy_status": "discarded",
        "items": [("forms/travel-old.md", "delete")],
    },
    {
        "id": "cs-ready",
        "summary": "Add the parental leave FAQ",
        "state": "ready",
        "legacy_status": "draft",
        "items": [
            ("faq/parental-leave.md", "create"),
            ("faq/index.md", "update"),
        ],
    },
]


@dataclass
class ParityReport:
    rows: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(row["match"] for row in self.rows)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "compared_fields": list(COMPARED_FIELDS),
            "scope": "seeded-sqlite-fixture (not the production copy)",
            "rows": self.rows,
        }


def seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS drafts;
            DROP TABLE IF EXISTS change_sets;
            DROP TABLE IF EXISTS change_set_items;
            CREATE TABLE drafts (
                id TEXT PRIMARY KEY, draft_type TEXT, title TEXT, content TEXT, status TEXT
            );
            CREATE TABLE change_sets (id TEXT PRIMARY KEY, summary TEXT, state TEXT);
            CREATE TABLE change_set_items (
                change_set_id TEXT, normalized_path TEXT, operation TEXT
            );
            """
        )
        for row in FIXTURE:
            # The legacy Draft row is what an old reader would see.
            conn.execute(
                "INSERT INTO drafts VALUES (?,?,?,?,?)",
                (row["id"], "file_change_set", row["summary"], "", row["legacy_status"]),
            )
            conn.execute(
                "INSERT INTO change_sets VALUES (?,?,?)",
                (row["id"], row["summary"], row["state"]),
            )
            conn.executemany(
                "INSERT INTO change_set_items VALUES (?,?,?)",
                [(row["id"], path, op) for path, op in row["items"]],
            )
        conn.commit()


def compare(db_path: Path) -> ParityReport:
    report = ParityReport()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for legacy in conn.execute("SELECT * FROM drafts ORDER BY id"):
            change_set = conn.execute(
                "SELECT * FROM change_sets WHERE id = ?", (legacy["id"],)
            ).fetchone()
            if change_set is None:
                report.rows.append(
                    {
                        "id": legacy["id"],
                        "match": False,
                        "mismatches": [{"field": "id", "reason": "no change set"}],
                    }
                )
                continue
            items = [
                SimpleNamespace(normalized_path=item["normalized_path"], operation=item["operation"])
                for item in conn.execute(
                    "SELECT normalized_path, operation FROM change_set_items "
                    "WHERE change_set_id = ? ORDER BY normalized_path",
                    (legacy["id"],),
                )
            ]
            projected = project_change_set(
                SimpleNamespace(
                    id=change_set["id"], state=change_set["state"], summary=change_set["summary"]
                ),
                items,
            )
            expected = {
                "draft_type": legacy["draft_type"],
                "status": legacy["status"],
                "title": legacy["title"],
                "related_sources": sorted(
                    [
                        {"path": item["normalized_path"], "operation": item["operation"]}
                        for item in conn.execute(
                            "SELECT normalized_path, operation FROM change_set_items "
                            "WHERE change_set_id = ?",
                            (legacy["id"],),
                        )
                    ],
                    key=lambda entry: entry["path"],
                ),
            }
            mismatches = [
                {
                    "field": name,
                    "legacy": expected[name],
                    "projected": sorted(projected[name], key=lambda e: e["path"])
                    if name == "related_sources"
                    else projected[name],
                }
                for name in COMPARED_FIELDS
                if (
                    sorted(projected[name], key=lambda e: e["path"])
                    if name == "related_sources"
                    else projected[name]
                )
                != expected[name]
            ]
            report.rows.append(
                {"id": legacy["id"], "match": not mismatches, "mismatches": mismatches}
            )
    return report


def main() -> int:
    db_path = Path("artifacts/acceptance/legacy-read-parity.sqlite")
    if db_path.exists():
        db_path.unlink()
    seed(db_path)
    report = compare(db_path)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"written": str(OUT), "ok": report.ok, "rows": len(report.rows)}))
    for row in report.rows:
        if not row["match"]:
            print(f"  mismatch {row['id']}: {row['mismatches']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
