"""T152 preflight: list code that still reads the legacy ``drafts`` columns.

**Read-only.** It never alters a schema; it answers "who would break if
``draft_type`` / ``related_sources`` / ``source_question`` were contracted away",
which is the prerequisite T152 asks for before the destructive migration.

The first version of this script reported every line matching
``Draft|drafts|draft.<legacy column>`` and produced a flat count (45 at the time),
with the gate stuck shut.  Most of those hits are *lookalikes* that have nothing to
do with the legacy table: the FAQ draft table, the ``generated_draft`` material
source type, the ``draft`` state of a ChangeSet, the ``draft`` router task type, the
mock MCP e-mail draft, and local variables named ``draft`` in the upload saga.  A
removal decision made on that count would be wrong in both directions, so every hit
is now classified and two separate gates are reported:

* ``columns_gate`` -- code that touches the three contract-away columns.  This is
  the gate T152 itself needs.
* ``path_gate`` -- code that touches the ``Draft`` model / ``drafts`` table at all.
  This is the wider gate T159/T160 need before deleting the legacy path.

Exit code 1 when either gate still has legacy consumers, 0 when both are clear.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path("artifacts/acceptance/t152-consumers.json")

#: Columns T152 wants to contract, and the model that owns them.
LEGACY_COLUMNS = ("draft_type", "related_sources", "source_question")
DEFINITION_FILE = Path("backend/app/db/models.py")
EXCLUDED_PARTS = {"alembic", "versions", "tests", "scripts", "node_modules", "__pycache__"}

#: Only the ``Draft`` model itself, raw ``drafts`` SQL, or attribute access on a
#: draft variable counts; a generic ``.content`` is usually an LLM completion and
#: would drown the report in false positives.
PATTERN = re.compile(
    r"\bDraft\b|\bdrafts\b|\bdraft\."
    + "(?:"
    + "|".join(LEGACY_COLUMNS)
    + "|content"
    + r")\b"
)

#: Direct hits on the columns that T152 wants to contract.
COLUMN_PATTERN = re.compile(r"\b(?:" + "|".join(LEGACY_COLUMNS) + r")\b")

#: Files that are the legacy Draft surface itself: model exports, its own service,
#: its routes and schemas, the counted compatibility projection and the telemetry
#: that renders it.  Every line here is a genuine consumer.
LEGACY_PATH_FILES = {
    "backend/app/api/routes_draft.py",
    "backend/app/db/base.py",
    "backend/app/observability/compatibility.py",
    "backend/app/schemas/draft.py",
    "backend/app/services/draft_projection.py",
    "backend/app/services/draft_service.py",
}

#: Files that both define the legacy tool surface and count its writes.
LEGACY_TOOL_FILES = {
    "backend/app/tools/builtin_tools.py",
    "backend/app/tools/chat_tools.py",
    "backend/app/main.py",
    "backend/app/db/init_db.py",
}

#: Databases seeded/migrated by ``init_db`` mention ``drafts`` as a table name and
#: ``draft.create`` as a permission code; those are wired to the legacy path too.

FAQ_DRAFT_PATTERN = re.compile(r"faq[-_ ]?drafts?|FAQDraft", re.IGNORECASE)
MATERIAL_SOURCE_PATTERN = re.compile(r"generated_draft")
MOCK_PATTERN = re.compile(r"mock")
CHANGE_SET_STATE_PATTERN = re.compile(r"\"ready\"|\"awaiting_approval\"|\"discarded\"|_STATE_PROJECTION")
TASK_TAXONOMY_PATTERN = re.compile(r"task_type|tool_hints|草稿|邮件")


def classify(relative: str, line: str) -> str:
    """Bucket one hit.  Order matters: the most specific label wins."""
    if relative in LEGACY_PATH_FILES:
        return "legacy_draft"
    if relative in LEGACY_TOOL_FILES:
        if MATERIAL_SOURCE_PATTERN.search(line):
            return "material_source_type"
        if relative == "backend/app/db/init_db.py" and "faq" in line.lower():
            return "faq_draft"
        return "legacy_draft"
    if MATERIAL_SOURCE_PATTERN.search(line):
        return "material_source_type"
    if FAQ_DRAFT_PATTERN.search(line):
        return "faq_draft"
    if relative.startswith("backend/app/mcp/") or MOCK_PATTERN.search(line):
        return "mock_connector"
    if CHANGE_SET_STATE_PATTERN.search(line):
        return "change_set_state"
    if relative.startswith("backend/app/agents/"):
        return "task_taxonomy"
    if relative in {
        "backend/app/api/routes_materials.py",
        "backend/app/services/document_service.py",
    }:
        return "upload_saga_local_var"
    if relative == "backend/app/approvals/service.py":
        return "change_set_state"
    return "needs_review"


def scan(root: Path) -> list[dict]:
    consumers: list[dict] = []
    for path in sorted((root / "backend").rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path == root / DEFINITION_FILE:
            continue
        relative = path.relative_to(root).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not PATTERN.search(line):
                continue
            consumers.append(
                {
                    "file": relative,
                    "line": number,
                    "category": classify(relative, line),
                    "touches_legacy_column": bool(COLUMN_PATTERN.search(line)),
                    "text": stripped[:160],
                }
            )
    return consumers


def main() -> int:
    consumers = scan(Path.cwd())
    by_category: dict[str, int] = {}
    for consumer in consumers:
        by_category[consumer["category"]] = by_category.get(consumer["category"], 0) + 1

    legacy = [c for c in consumers if c["category"] == "legacy_draft"]
    column_consumers = [c for c in consumers if c["touches_legacy_column"]]
    needs_review = [c for c in consumers if c["category"] == "needs_review"]

    columns_gate_cleared = not column_consumers
    path_gate_cleared = not legacy and not needs_review

    payload = {
        "destructive": False,
        "gate_cleared": columns_gate_cleared and path_gate_cleared,
        "columns_gate_cleared": columns_gate_cleared,
        "path_gate_cleared": path_gate_cleared,
        "scanned_root": "backend/",
        "excluded": sorted(EXCLUDED_PARTS),
        "legacy_columns": list(LEGACY_COLUMNS),
        "consumer_count": len(consumers),
        "legacy_path_consumers": len(legacy),
        "legacy_column_consumers": len(column_consumers),
        "needs_review": len(needs_review),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "consumers": consumers,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "written": str(OUT),
                "gate_cleared": payload["gate_cleared"],
                "total_hits": payload["consumer_count"],
                "legacy_path_consumers": payload["legacy_path_consumers"],
                "legacy_column_consumers": payload["legacy_column_consumers"],
                "needs_review": payload["needs_review"],
                "by_category": payload["by_category"],
            },
            ensure_ascii=False,
        )
    )
    for consumer in legacy[:10] + needs_review[:10]:
        print(f"  [{consumer['category']}] {consumer['file']}:{consumer['line']} {consumer['text']}")
    return 0 if payload["gate_cleared"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
