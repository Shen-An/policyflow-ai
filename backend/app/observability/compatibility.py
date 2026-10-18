"""T158 legacy-surface usage ledger and the Stage 9 deletion gate.

Stage 9 may delete the pre-enterprise paths only after each of them has been
unused for a full release window.  Each subsystem already keeps its own counter
(:class:`~backend.app.storage.authority.StorageAuthorityTelemetry`,
:class:`~backend.app.graph.compat.AdapterUsageTelemetry`,
:class:`~backend.app.services.draft_projection.DraftLegacyTelemetry`); this
module is the one place that answers the combined question.

Deliberate choices:

* Counts are **never inferred**.  An unmeasured surface is ``None``, not ``0``,
  because "we did not look" must not clear code for deletion.
* The gate needs both conditions: a completed window *and* zero measured use on
  every surface.  A fresh process trivially has zero counters, which is exactly
  why the window matters.
* A snapshot is data, not a verdict: it records the window it was taken over so
  the decision can be replayed rather than trusted.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "LEGACY_SURFACES",
    "CompatibilitySnapshot",
    "LegacySurfaceCounters",
    "ReleaseWindow",
    "build_snapshot",
    "default_window",
]

#: Surfaces that carry a `<code>` removal condition in the plan.  Data, not prose,
#: so a new adapter has to be registered here to be considered.
LEGACY_SURFACES: dict[str, str] = {
    "legacy_chat_adapter": "旧 /api/chat 适配器（LegacyGraphAdapter, chat）",
    "legacy_eval_adapter": "旧评估入口适配器（LegacyGraphAdapter, eval）",
    "agent_pipeline_facade": "AgentPipeline facade 与第二套 stage",
    "local_material_workspace": "本机文件写入（UPLOAD_DIR）",
    "lightrag_production_workspace": "LightRAG 生产 workspace 索引",
    "draft_projection": "Draft 兼容投影的旧写入路径",
    "browser_shell": "浏览器 SPA（Electron 之外的 user surface）",
}


@dataclass(frozen=True, slots=True)
class ReleaseWindow:
    """The observation period the counters must cover."""

    start: datetime
    end: datetime
    label: str = "release-window"

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("release window end must be after start")

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86_400

    def elapsed(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.end

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": round(self.days, 3),
        }


def default_window(days: int = 14, *, now: datetime | None = None) -> ReleaseWindow:
    """A window ending now; ``elapsed`` is False until ``days`` have passed."""

    end = now or datetime.now(UTC)
    return ReleaseWindow(start=end - timedelta(days=days), end=end)


class LegacySurfaceCounters:
    """Durable per-process counters for the two surfaces without a telemetry object.

    ``browser_shell`` is counted from ``GET /`` document loads whose User-Agent is
    not Electron; ``agent_pipeline_facade`` is counted at the facade entrypoint.
    The ledger exists so a later audit can read a number even though the process
    that produced it is gone. A missing ledger means *not measured* — the audit
    must not read it as zero.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._counts: Counter[str] = Counter()
        self.updated_at: datetime | None = None
        self._load()

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        surfaces = payload.get("surfaces", payload)
        for name, count in surfaces.items():
            if name in LEGACY_SURFACES and isinstance(count, int):
                self._counts[name] = count
        raw = payload.get("updated_at")
        self.updated_at = datetime.fromisoformat(raw) if isinstance(raw, str) else None

    def record(self, surface: str) -> None:
        if surface not in LEGACY_SURFACES:
            raise ValueError(f"unknown legacy surface {surface!r}")
        self._counts[surface] += 1
        self._flush()

    def count(self, surface: str) -> int:
        return int(self._counts.get(surface, 0))

    def reset(self) -> None:
        self._counts.clear()
        self._flush()

    def _flush(self) -> None:
        self.updated_at = datetime.now(UTC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": self.updated_at.isoformat(),
            "surfaces": dict(sorted(self._counts.items())),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@dataclass(slots=True)
class CompatibilitySnapshot:
    window: ReleaseWindow
    usage: dict[str, int | None]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        unknown = set(self.usage) - set(LEGACY_SURFACES)
        if unknown:
            raise ValueError(f"unknown legacy surfaces: {sorted(unknown)}")
        missing = set(LEGACY_SURFACES) - set(self.usage)
        if missing:
            raise ValueError(f"legacy surfaces not measured: {sorted(missing)}")

    def measured(self) -> dict[str, int]:
        return {name: count for name, count in self.usage.items() if count is not None}

    def unmeasured(self) -> list[str]:
        return [name for name, count in self.usage.items() if count is None]

    def zero_use(self) -> bool:
        if self.unmeasured():
            return False
        return all(count == 0 for count in self.usage.values())

    def eligible_for_removal(self, now: datetime | None = None) -> bool:
        return self.window.elapsed(now) and self.zero_use()

    def blockers(self, now: datetime | None = None) -> list[str]:
        reasons: list[str] = []
        if not self.window.elapsed(now):
            reasons.append(f"release window {self.window.label} 尚未结束（{self.window.days:.1f} 天）")
        for name in self.unmeasured():
            reasons.append(f"{name} 未测量")
        for name, count in self.measured().items():
            if count:
                reasons.append(f"{name} 仍有 {count} 次使用")
        return reasons

    def as_dict(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "window": self.window.as_dict(),
            "surfaces": {
                name: {"description": LEGACY_SURFACES[name], "usage": count}
                for name, count in self.usage.items()
            },
            "zero_use": self.zero_use(),
            "eligible_for_removal": self.eligible_for_removal(now),
            "blockers": self.blockers(now),
            "notes": list(self.notes),
        }


def build_snapshot(
    window: ReleaseWindow,
    *,
    storage: Any | None = None,
    adapters: Any | None = None,
    drafts: Any | None = None,
    counters: LegacySurfaceCounters | None = None,
    browser_shell_requests: int | None = None,
    pipeline_facade_uses: int | None = None,
    now: datetime | None = None,
) -> CompatibilitySnapshot:
    """Combine the subsystem counters into one ledger.

    ``None`` for an argument means *that surface was not measured in this run* —
    the result then cannot clear the gate.  Passing a telemetry object reads its
    live counters; nothing here synthesises a number.  ``counters`` (the durable
    ledger) wins over the explicit integers when the ledger file exists.
    """

    if counters is not None and counters.exists:
        browser_shell_requests = counters.count("browser_shell")
        pipeline_facade_uses = counters.count("agent_pipeline_facade")

    usage: dict[str, int | None] = {
        "legacy_chat_adapter": (
            adapters.usage_count("legacy_chat") if adapters is not None else None
        ),
        "legacy_eval_adapter": (
            adapters.usage_count("legacy_eval") if adapters is not None else None
        ),
        "agent_pipeline_facade": pipeline_facade_uses,
        "local_material_workspace": (
            storage.usage_count("local_file_write") if storage is not None else None
        ),
        "lightrag_production_workspace": (
            storage.usage_count("lightrag_index") if storage is not None else None
        ),
        "draft_projection": drafts.total_usage() if drafts is not None else None,
        "browser_shell": browser_shell_requests,
    }
    notes = [
        "counters are per-process unless the caller wired a durable ledger",
        "browser_shell is measured from the reverse-proxy access log, not this process",
    ]
    return CompatibilitySnapshot(window=window, usage=usage, notes=notes, generated_at=now or datetime.now(UTC))
