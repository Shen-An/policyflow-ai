"""T152 preflight: list code that still reads the legacy ``drafts`` columns.

**Read-only.** It never alters a schema; it answers "who would break if
``draft_type`` / ``related_sources`` / ``content`` / ``source_question`` were
contracted away", which is the prerequisite T152 asks for before the destructive
migration.  Exit code 1 when consumers remain (gate not cleared), 0 when none do.
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


def scan(root: Path) -> list[dict]:
    consumers: list[dict] = []
    for path in sorted((root / "backend").rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path == root / DEFINITION_FILE:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if PATTERN.search(line):
                consumers.append(
                    {
                        "file": path.relative_to(root).as_posix(),
                        "line": number,
                        "text": stripped[:160],
                    }
                )
    return consumers


def main() -> int:
    consumers = scan(Path.cwd())
    payload = {
        "destructive": False,
        "gate_cleared": not consumers,
        "scanned_root": "backend/",
        "excluded": sorted(EXCLUDED_PARTS),
        "legacy_columns": list(LEGACY_COLUMNS),
        "consumer_count": len(consumers),
        "consumers": consumers,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(OUT), "gate_cleared": payload["gate_cleared"],
                      "consumers": len(consumers)}))
    for consumer in consumers[:10]:
        print(f"  {consumer['file']}:{consumer['line']} {consumer['text']}")
    return 0 if payload["gate_cleared"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
