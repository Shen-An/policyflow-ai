"""Production Chat/Eval → shared-graph route adapter (T051/T052).

This is the live-endpoint counterpart to the shadow-mode
:class:`~backend.app.graph.legacy_adapter.LegacyGraphAdapter`. When the
``ROUTE_VIA_GRAPH_ADAPTER`` setting is on, ``routes_chat`` / ``routes_eval`` stop
calling the orchestration service functions directly and instead go through this
adapter, which:

1. records an :class:`~backend.app.graph.legacy_adapter.AdapterTelemetryEvent`
   on the shared :class:`~backend.app.graph.compat.AdapterUsageTelemetry`
   counter — the Stage 9 *removal-ledger* signal (see ``compat.py``); and
2. delegates to the existing shared pipeline-graph path (``send_chat_message`` /
   ``iter_chat_events`` / ``execute_eval_run``), so the rich ``ChatResponse`` /
   eval contract is produced unchanged.

Honesty boundary (docs/08 §10, tasks.md Phase 3 落地状态):

- The underlying execution here is the **pipeline orchestration graph**
  (``AgentPipeline.run``, now graph-driven — Option A), *not* the durable
  evidence-path ``GraphService``. The durable graph cannot yet produce the rich
  router/skill/compliance/plan output surface the frontend contract needs;
  routing production onto it is Stage 9 / requires the retrieval stack.
- Therefore this adapter is a **reversible indirection + telemetry** layer, not
  a claim that production now runs on ``GraphService``. The flag defaults off,
  so the verified legacy path is byte-identical until the switch is flipped.
- Telemetry records adapter name, mode, tenant and run id only — never message
  or answer content (same guarantee as the shadow adapter).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.app.graph.legacy_adapter import AdapterMode, AdapterTelemetryEvent

if TYPE_CHECKING:
    from sqlmodel import Session

    from backend.app.agents.pipeline import AgentPipeline
    from backend.app.db.models import User
    from backend.app.graph.compat import AdapterUsageTelemetry
    from backend.app.schemas.chat import ChatRequest, ChatResponse
    from backend.app.schemas.eval import EvalRunCreate

__all__ = ["GraphRouteAdapter"]


class GraphRouteAdapter:
    """Routes live Chat/Eval endpoints through the shared graph with telemetry."""

    def __init__(self, telemetry: AdapterUsageTelemetry | None) -> None:
        self._telemetry = telemetry

    def _record(self, adapter: str, tenant_id: str, run_id: str) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record(
            AdapterTelemetryEvent(
                adapter=adapter,
                mode=AdapterMode.ACTIVE,
                tenant_id=tenant_id or "",
                run_id=run_id or "",
            )
        )

    async def chat(
        self,
        *,
        session: Session,
        user: User,
        data: ChatRequest,
        pipeline: AgentPipeline,
        **deps: Any,
    ) -> ChatResponse:
        from backend.app.services.chat_service import send_chat_message

        response = await send_chat_message(session, user, data, pipeline, **deps)
        self._record(
            "route_chat",
            getattr(user, "tenant_id", ""),
            str(getattr(response, "query_log_id", "") or ""),
        )
        return response

    async def chat_events(
        self,
        *,
        session: Session,
        user: User,
        data: ChatRequest,
        pipeline: AgentPipeline,
        **deps: Any,
    ) -> AsyncIterator[tuple[str, Any]]:
        from backend.app.services.chat_service import iter_chat_events

        # Record at stream entry: the authoritative run id is only known once the
        # final event lands, and telemetry deliberately carries no payload.
        self._record("route_chat_stream", getattr(user, "tenant_id", ""), "")
        async for event in iter_chat_events(session, user, data, pipeline, **deps):
            yield event

    async def run_eval(
        self,
        *,
        engine: Any,
        rag_service: Any,
        pipeline: AgentPipeline,
        run_id: str,
        data: EvalRunCreate,
        tenant_id: str,
    ) -> None:
        from backend.app.services.eval_service import execute_eval_run

        self._record("route_eval", tenant_id, str(run_id or ""))
        await execute_eval_run(engine, rag_service, pipeline, run_id, data)
