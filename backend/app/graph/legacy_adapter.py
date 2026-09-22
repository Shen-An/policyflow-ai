"""Temporary legacy Chat/Eval → shared-graph adapter (T042, T051–T053).

This adapter is the *only* place the old Chat/Eval response shapes are produced;
the shared graph is the single decision path underneath. It exists so existing
clients keep working while callers migrate, and it is on the removal ledger —
it must expose usage telemetry and a Stage 9 deletion condition, never become a
second orchestration.

Two safety properties are enforced here:

- **Shadow mode disables every side effect.** A shadow run exercises the
  decision path for parity comparison but must not call tools, write back
  memory, touch files or hit connectors — so all four capability flags are set
  false and the graph performs none.
- **Telemetry carries no payload content.** Compatibility events record the
  adapter name, mode, tenant and run id only; the message and answer never
  enter telemetry (asserted via ``repr``), so migration monitoring cannot leak
  employee questions or answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.app.graph.service import GraphRunner, GraphRunRequest

__all__ = [
    "AdapterMode",
    "AdapterTelemetryEvent",
    "LegacyChatRequest",
    "LegacyChatResponse",
    "LegacyEvalRequest",
    "LegacyEvalResponse",
    "LegacyGraphAdapter",
    "RecordingAdapterTelemetry",
]


class AdapterMode(str, Enum):
    ACTIVE = "active"
    SHADOW = "shadow"


@dataclass(frozen=True)
class LegacyChatRequest:
    tenant_id: str
    user_id: str
    conversation_id: str
    message: str


@dataclass(frozen=True)
class LegacyChatResponse:
    answer: str
    conversation_id: str
    run_id: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class LegacyEvalRequest:
    tenant_id: str
    user_id: str
    eval_case_id: str
    question: str


@dataclass(frozen=True)
class LegacyEvalResponse:
    eval_case_id: str
    answer: str
    evidence_gate: str


@dataclass(frozen=True)
class AdapterTelemetryEvent:
    """Compatibility-usage signal. Deliberately holds no payload content."""

    adapter: str
    mode: AdapterMode
    tenant_id: str
    run_id: str
    parity_match: bool | None = None


class RecordingAdapterTelemetry:
    def __init__(self) -> None:
        self.events: list[AdapterTelemetryEvent] = []

    def record(self, event: AdapterTelemetryEvent) -> None:
        self.events.append(event)


class LegacyGraphAdapter:
    """Maps legacy Chat/Eval calls onto the shared graph runner."""

    def __init__(
        self,
        *,
        graph: GraphRunner,
        telemetry: RecordingAdapterTelemetry,
        mode: AdapterMode = AdapterMode.ACTIVE,
    ) -> None:
        self._graph = graph
        self._telemetry = telemetry
        self._mode = mode

    @property
    def _side_effects_allowed(self) -> bool:
        return self._mode is AdapterMode.ACTIVE

    async def chat(self, request: LegacyChatRequest) -> LegacyChatResponse:
        allowed = self._side_effects_allowed
        run_request = GraphRunRequest(
            entrypoint="chat",
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            run_id="",  # authoritative run id comes back from the graph result
            conversation_id=request.conversation_id,
            input_payload={"message": request.message},
            deterministic=False,
            allow_tools=allowed,
            allow_writeback=allowed,
            allow_files=allowed,
            allow_connectors=allowed,
        )
        result = await self._graph.run(run_request)
        self._telemetry.record(
            AdapterTelemetryEvent(
                adapter="legacy_chat",
                mode=self._mode,
                tenant_id=request.tenant_id,
                run_id=result.run_id,
            )
        )
        return LegacyChatResponse(
            answer=result.answer,
            conversation_id=request.conversation_id,
            run_id=result.run_id,
            evidence=result.evidence,
        )

    async def eval(self, request: LegacyEvalRequest) -> LegacyEvalResponse:
        # Eval is always deterministic and side-effect free, independent of
        # adapter mode; it still cannot bypass the shared evidence gate.
        run_request = GraphRunRequest(
            entrypoint="eval",
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            run_id="",
            input_payload={"question": request.question, "eval_case_id": request.eval_case_id},
            deterministic=True,
            allow_tools=False,
            allow_writeback=False,
            allow_files=False,
            allow_connectors=False,
        )
        result = await self._graph.run(run_request)
        self._telemetry.record(
            AdapterTelemetryEvent(
                adapter="legacy_eval",
                mode=self._mode,
                tenant_id=request.tenant_id,
                run_id=result.run_id,
            )
        )
        return LegacyEvalResponse(
            eval_case_id=request.eval_case_id,
            answer=result.answer,
            evidence_gate=result.evidence_gate,
        )
