"""Write the T158 legacy-surface usage ledger.

Run from the repo root:

    python scripts/acceptance_legacy_audit.py

The script only *reports*.  It exits 0 even when the deletion gate is closed,
because "gate closed" is the expected state until a release window has elapsed;
CI should read `eligible_for_removal` from the JSON rather than the exit code.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from backend.app.graph.compat import AdapterUsageTelemetry
from backend.app.observability.compatibility import (
    CompatibilitySnapshot,
    LegacySurfaceCounters,
    ReleaseWindow,
    build_snapshot,
)
from backend.app.services.draft_projection import DraftLegacyTelemetry
from backend.app.storage.authority import StorageAuthorityTelemetry

OUT = Path("artifacts/acceptance/legacy-exit.json")
LEDGER = Path(os.environ.get("POLICYFLOW_LEGACY_LEDGER", "artifacts/acceptance/legacy-usage.json"))

#: Stage 9 landed 2026-10-18; one release window is the following month.
WINDOW = ReleaseWindow(
    start=datetime(2026, 10, 18, tzinfo=UTC),
    end=datetime(2026, 11, 18, tzinfo=UTC),
    label="stage9-release-window",
)


def main() -> int:
    counters = LegacySurfaceCounters(LEDGER)
    snapshot: CompatibilitySnapshot = build_snapshot(
        WINDOW,
        storage=StorageAuthorityTelemetry(),
        adapters=AdapterUsageTelemetry(),
        drafts=DraftLegacyTelemetry(),
        counters=counters,
    )
    payload = snapshot.as_dict()
    payload["ledger"] = {"path": str(LEDGER), "exists": counters.exists}
    payload["how_to_measure"] = {
        "browser_shell": "应用中间件对 `GET /` 且非 Electron UA 的 HTML 文档请求计数",
        "agent_pipeline_facade": "AgentPipeline.run() 入口计数",
        "ledger_absent": "ledger 不存在 = 未测量（不是 0），门禁不放行",
        "others": "由各 telemetry 对象提供；本脚本只读不造数",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(OUT), "eligible_for_removal": payload["eligible_for_removal"]}, ensure_ascii=False))
    for reason in payload["blockers"]:
        print(f"  blocked: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
