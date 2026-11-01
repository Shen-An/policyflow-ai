"""Summarize shadow diff records: strict vs comparable parity, and why they differ."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DIR = Path("artifacts/graph/shadow")
OUT = Path("artifacts/acceptance/shadow-parity-summary.txt")


def main() -> int:
    records = []
    for path in sorted(DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        diff = data.get("diff") or {}
        records.append(
            {
                "file": path.name,
                "identifier": data.get("identifier"),
                "ok": bool((data.get("shadow") or {}).get("ok")),
                "error": (data.get("shadow") or {}).get("error"),
                "strict": bool(diff.get("match")),
                "comparable": bool(diff.get("comparable_match")),
                "mismatches": list(diff.get("field_mismatches") or []),
                "unexplained": list(diff.get("unexplained_mismatches") or []),
                "explained": dict(diff.get("explained_by_degradation") or {}),
                "events_match": bool((diff.get("event_sequence") or {}).get("match")),
                "first_divergence": (diff.get("event_sequence") or {}).get(
                    "first_divergence_index"
                ),
            }
        )

    lines = [f"records: {len(records)}"]
    for item in records:
        lines.append(
            f"- {item['file']}: ok={item['ok']} strict={item['strict']} "
            f"comparable={item['comparable']} mismatches={item['mismatches']} "
            f"unexplained={item['unexplained']} events_match={item['events_match']} "
            f"first_divergence={item['first_divergence']} error={item['error']}"
        )

    total = len(records)
    if total:
        lines.append("")
        lines.append(f"strict match:     {sum(1 for r in records if r['strict'])}/{total}")
        lines.append(
            f"comparable match: {sum(1 for r in records if r['comparable'])}/{total}"
        )
        lines.append(f"shadow ok:        {sum(1 for r in records if r['ok'])}/{total}")
        lines.append(
            f"event sequence:   {sum(1 for r in records if r['events_match'])}/{total}"
        )
        lines.append("")
        lines.append("mismatch field counts:")
        for field, count in sorted(
            Counter(f for r in records for f in r["mismatches"]).items()
        ):
            lines.append(f"  {field}: {count}")
        lines.append("unexplained field counts:")
        for field, count in sorted(
            Counter(f for r in records for f in r["unexplained"]).items()
        ):
            lines.append(f"  {field}: {count}")
        lines.append("")
        lines.append("explanations:")
        for field, count in sorted(
            Counter(f for r in records for f in r["explained"]).items()
        ):
            lines.append(f"  {field}: {count}")

    text = "\n".join(lines)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
