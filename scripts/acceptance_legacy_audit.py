"""Write the T158 legacy-surface usage ledger.

Run from the repo root:

    python scripts/acceptance_legacy_audit.py

The script only *reports*.  It exits 0 even when the deletion gate is closed,
because "gate closed" is the expected state until a release window has elapsed;
CI should read `eligible_for_removal` from the JSON rather than the exit code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.graph.compat import AdapterUsageTelemetry
from backend.app.observability.compatibility import (
    CompatibilitySnapshot,
    ReleaseWindow,
    build_snapshot,
)
from backend.app.services.draft_projection import DraftLegacyTelemetry
from backend.app.storage.authority import StorageAuthorityTelemetry

OUT = Path("artifacts/acceptance/legacy-exit.json")

#: Stage 9 landed 2026-10-18; one release window is the following month.
WINDOW = ReleaseWindow(
    start=datetime(2026, 10, 18, tzinfo=UTC),
    end=datetime(2026, 11, 18, tzinfo=UTC),
    label="stage9-release-window",
)


def main() -> int:
    snapshot: CompatibilitySnapshot = build_snapshot(
        WINDOW,
        storage=StorageAuthorityTelemetry(),
        adapters=AdapterUsageTelemetry(),
        drafts=DraftLegacyTelemetry(),
        # Not measurable from this process: no proxy access log, no facade counter.
        browser_shell_requests=None,
        pipeline_facade_uses=None,
    )
    payload = snapshot.as_dict()
    payload["how_to_measure"] = {
        "browser_shell": "reverse-proxy access log：统计非 Electron UA 对 / 的请求数",
        "agent_pipeline_facade": "在 facade 入口计数并落 durable ledger；本进程无法代表窗口",
        "others": "由各 telemetry 对象/durable ledger 提供；本脚本只读不造数",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(OUT), "eligible_for_removal": payload["eligible_for_removal"]}, ensure_ascii=False))
    for reason in payload["blockers"]:
        print(f"  blocked: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
