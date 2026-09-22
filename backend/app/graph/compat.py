"""Legacy → shared-graph compatibility wiring and usage telemetry (T051–T053).

The shared graph is the one decision path. During migration the legacy Chat and
Eval surfaces keep their response shapes by going through a
:class:`~backend.app.graph.legacy_adapter.LegacyGraphAdapter` over the single
:class:`~backend.app.graph.service.GraphService`. Every such call increments a
counter here.

That counter is not decoration: it is the Stage 9 *deletion condition*. The
legacy adapter, the ``AgentPipeline`` facade and these routes may only be
removed after :meth:`AdapterUsageTelemetry.zero_use_over_window` reports zero
direct legacy use across a release window (see the plan's Compatibility and
Removal Ledger). Until then the adapter coexists with — never replaces — the
verified legacy path.

The telemetry records adapter name, mode, tenant and run id only; it never
carries message or answer content.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from backend.app.graph.legacy_adapter import (
    AdapterMode,
    AdapterTelemetryEvent,
    LegacyGraphAdapter,
)
from backend.app.graph.service import GraphRunner

__all__ = [
    "AdapterUsageTelemetry",
    "build_chat_adapter",
    "build_eval_adapter",
]


@dataclass
class AdapterUsageTelemetry:
    """Counts legacy-adapter usage; drives the Stage 9 removal decision."""

    def __post_init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self.events: list[AdapterTelemetryEvent] = []

    def record(self, event: AdapterTelemetryEvent) -> None:
        self._counts[event.adapter] += 1
        # Keep only the metadata event; it holds no payload content by design.
        self.events.append(event)

    def usage_count(self, adapter: str) -> int:
        return self._counts[adapter]

    def zero_use_over_window(self) -> bool:
        """True only when no legacy adapter has been used — the removal gate."""
        return sum(self._counts.values()) == 0


def build_chat_adapter(
    graph: GraphRunner,
    telemetry: AdapterUsageTelemetry,
    *,
    mode: AdapterMode = AdapterMode.ACTIVE,
) -> LegacyGraphAdapter:
    return LegacyGraphAdapter(graph=graph, telemetry=telemetry, mode=mode)


def build_eval_adapter(
    graph: GraphRunner,
    telemetry: AdapterUsageTelemetry,
) -> LegacyGraphAdapter:
    # Eval is always deterministic and side-effect free at the adapter call site.
    return LegacyGraphAdapter(graph=graph, telemetry=telemetry, mode=AdapterMode.ACTIVE)
