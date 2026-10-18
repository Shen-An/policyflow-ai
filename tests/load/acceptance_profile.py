"""Acceptance load profile and threshold evaluation (T153).

The profile describes the Stage 9 acceptance run: 1,000 active sessions, 1,000
concurrent SSE streams, 200 non-LLM RPS, a 30 minute soak, file streaming and
tenant isolation.  Running that at scale needs a dedicated environment, so this
module keeps the **definition** and the **verdict logic** testable without
pretending the run happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Mix of virtual users. 1000 chat sessions + 1000 SSE streams; file and
#: isolation workloads ride along at a small share so their p95 is still sampled.
WORKFLOW_WEIGHTS: dict[str, float] = {
    "chat": 1000.0,
    "sse": 1000.0,
    "file": 100.0,
    "isolation": 100.0,
}


@dataclass(frozen=True, slots=True)
class AcceptanceTargets:
    """Thresholds from `spec.md` SC-004/SC-005 and the Stage 1 baseline."""

    active_sessions: int = 1_000
    sse_streams: int = 1_000
    non_llm_rps: int = 200
    non_llm_p95_ms: float = 500.0
    first_stage_p95_ms: float = 1_000.0
    sse_cleanup_seconds: float = 30.0
    soak_minutes: int = 30
    max_server_failure_rate: float = 0.01
    #: RSS may not grow more than this ratio across the soak (leak guard).
    max_rss_growth_ratio: float = 1.2

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_sessions": self.active_sessions,
            "sse_streams": self.sse_streams,
            "non_llm_rps": self.non_llm_rps,
            "non_llm_p95_ms": self.non_llm_p95_ms,
            "first_stage_p95_ms": self.first_stage_p95_ms,
            "sse_cleanup_seconds": self.sse_cleanup_seconds,
            "soak_minutes": self.soak_minutes,
            "max_server_failure_rate": self.max_server_failure_rate,
            "max_rss_growth_ratio": self.max_rss_growth_ratio,
        }


ACCEPTANCE_TARGETS = AcceptanceTargets()


@dataclass(slots=True)
class AcceptanceProfile:
    name: str = "acceptance"
    users: int = 2_200
    spawn_rate: float = 50.0
    wait_min_seconds: float = 0.0
    wait_max_seconds: float = 0.1
    workflow: str = "acceptance"
    targets: AcceptanceTargets = field(default_factory=AcceptanceTargets)
    workflow_weights: dict[str, float] = field(default_factory=lambda: dict(WORKFLOW_WEIGHTS))

    def options(self) -> dict[str, Any]:
        return {
            "profile": self.name,
            "users": self.users,
            "spawn_rate": self.spawn_rate,
            "wait_time": [self.wait_min_seconds, self.wait_max_seconds],
            "workflow": self.workflow,
            "workflow_weights": dict(self.workflow_weights),
            "targets": self.targets.as_dict(),
            "requires_dedicated_environment": True,
        }


def _collect(summary: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Pull the metrics we grade, reporting which ones are absent."""

    wanted = {
        "non_llm_rps": "non_llm_rps",
        "non_llm_p95_ms": "non_llm_p95_ms",
        "first_stage_p95_ms": "first_stage_p95_ms",
        "server_failure_rate": "server_failure_rate",
        "sse_cleanup_seconds": "sse_cleanup_seconds",
        "soak_minutes": "soak_minutes",
        "rss_growth_ratio": "rss_growth_ratio",
    }
    values: dict[str, float] = {}
    missing: list[str] = []
    for key, source in wanted.items():
        raw = summary.get(source)
        if raw is None:
            missing.append(source)
            continue
        try:
            values[key] = float(raw)
        except (TypeError, ValueError):
            missing.append(source)
    return values, missing


def evaluate_acceptance(
    summary: dict[str, Any], targets: AcceptanceTargets = ACCEPTANCE_TARGETS
) -> dict[str, Any]:
    """Grade a load summary against the acceptance thresholds.

    Returns ``{"verdict": pass|fail|inconclusive, "checks": {...}, "missing": [...]}``.
    A missing metric is **inconclusive**, never a pass — that is the whole point
    of keeping this function separate from the run.
    """

    values, missing = _collect(summary)
    checks: dict[str, dict[str, Any]] = {}

    def gate(name: str, metric: str, ok: bool) -> None:
        checks[name] = {
            "metric": metric,
            "observed": values.get(metric),
            "verdict": "pass" if ok else "fail",
        }

    if "non_llm_rps" in values:
        gate("throughput", "non_llm_rps", values["non_llm_rps"] >= targets.non_llm_rps)
    if "non_llm_p95_ms" in values:
        gate("non_llm_latency", "non_llm_p95_ms", values["non_llm_p95_ms"] < targets.non_llm_p95_ms)
    if "first_stage_p95_ms" in values:
        gate(
            "first_stage_latency",
            "first_stage_p95_ms",
            values["first_stage_p95_ms"] < targets.first_stage_p95_ms,
        )
    if "server_failure_rate" in values:
        gate(
            "server_failures",
            "server_failure_rate",
            values["server_failure_rate"] < targets.max_server_failure_rate,
        )
    if "sse_cleanup_seconds" in values:
        gate(
            "sse_cleanup",
            "sse_cleanup_seconds",
            values["sse_cleanup_seconds"] < targets.sse_cleanup_seconds,
        )
    if "soak_minutes" in values:
        gate("soak_duration", "soak_minutes", values["soak_minutes"] >= targets.soak_minutes)
    if "rss_growth_ratio" in values:
        gate(
            "soak_leak_guard",
            "rss_growth_ratio",
            values["rss_growth_ratio"] <= targets.max_rss_growth_ratio,
        )

    failed = [name for name, entry in checks.items() if entry["verdict"] == "fail"]
    if failed:
        verdict = "fail"
    elif missing:
        verdict = "inconclusive"
    else:
        verdict = "pass"
    return {
        "verdict": verdict,
        "checks": checks,
        "missing": missing,
        "targets": targets.as_dict(),
    }
