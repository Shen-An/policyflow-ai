"""T053 slice 1: shadow-mode diff capture (contract).

Two properties have to hold, otherwise the shadow comparison is decoration:

1. With ``GRAPH_SHADOW_MODE`` on, a shadow run that disagrees with the
   authoritative run produces a structured diff record on disk (ChatResponse
   field digest + SSE event sequence) and a ``shadow_chat`` telemetry event with
   ``parity_match=False``. Divergence must be *observable*, not assumed away.
2. With the flag off (the default) the route adapter is a pass-through: the real
   pipeline object is handed downstream untouched, no second graph run happens,
   no record is written and no shadow telemetry is emitted.

The stub pipeline below deliberately diverges — the shadow replay answers
differently and emits fewer events — because a stub that agrees on everything
would only prove the diff ignores its inputs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.graph.compat import AdapterUsageTelemetry
from backend.app.graph.legacy_adapter import AdapterMode
from backend.app.graph.route_adapter import GraphRouteAdapter
from backend.app.graph.shadow import SHADOW_SCHEMA_VERSION, ChatShadowRunner
from backend.app.schemas.chat import RouterResult

pytestmark = pytest.mark.asyncio


class _StubChatResponse(BaseModel):
    """Minimal stand-in for ``ChatResponse`` — exposes ``model_dump`` like the real one."""

    query_log_id: str
    answer: str


class _StubRouterAgent:
    """Fixed router decision — no LLM, no retrieval."""

    llm_service = None

    async def run(self, question, knowledge_bases, *args, **kwargs):
        return RouterResult(domain="hr", difficulty="simple", reasoning_mode="cot_direct")


def _stub_result(answer: str) -> SimpleNamespace:
    """Duck-typed stand-in for ``PipelineResult`` (the digest only reads attrs)."""

    return SimpleNamespace(
        status="completed",
        reasoning_mode="cot_direct",
        router_result=RouterResult(domain="hr"),
        retrieval_result=SimpleNamespace(evidence=[]),
        answer_result=SimpleNamespace(answer=answer, confidence_score=0.5, tool_trace=[]),
        compliance=None,
        plan=[],
        skill_results=[],
        suggested_skills=[],
        errors=[],
    )


class _DivergentPipeline:
    """Authoritative run says one thing; the shadow replay (via ``_execute_plan``) another."""

    def __init__(self) -> None:
        self.router_agent = _StubRouterAgent()
        self.authoritative_calls = 0
        self.execute_plan_calls = 0

    def _graph_flags(self) -> dict:
        # Planning/ToT/L2 all off so the shadow replay is a single route→execute pass.
        return {
            "planning_enabled": False,
            "max_steps": 5,
            "tot_enabled": False,
            "tot_auto": False,
            "tot_min": 2,
            "tot_max": 3,
            "l2_enabled": False,
            "parallel_enabled": False,
        }

    async def _emit_stage(self, on_stage, stage, status, message):
        if on_stage is not None:
            await on_stage(stage, status, message)

    async def _emit_event(self, on_event, event, payload):
        if on_event is not None:
            await on_event(event, payload)

    async def run(self, *args, **kwargs):
        self.authoritative_calls += 1
        on_stage = kwargs.get("on_stage")
        on_event = kwargs.get("on_event")
        if on_stage is not None:
            await on_stage("AnswerAgent", "success", "生成回答")
        if on_event is not None:
            await on_event("final", {"answer": "权威答案"})
        return _stub_result("权威答案")

    async def _execute_plan(self, *args, **kwargs):
        self.execute_plan_calls += 1
        return _stub_result("影子答案")


async def test_shadow_mode_records_diff_when_paths_disagree(monkeypatch, tmp_path):
    telemetry = AdapterUsageTelemetry()
    runner = ChatShadowRunner(telemetry=telemetry, output_dir=tmp_path)
    adapter = GraphRouteAdapter(telemetry, shadow=runner)
    pipeline = _DivergentPipeline()

    async def fake_send_chat_message(session, user, data, pipeline_arg, **deps):
        result = await pipeline_arg.run(data.question, [], None)
        return _StubChatResponse(query_log_id="q-1", answer=result.answer_result.answer)

    monkeypatch.setattr(
        "backend.app.services.chat_service.send_chat_message", fake_send_chat_message
    )

    response = await adapter.chat(
        session=SimpleNamespace(),
        user=SimpleNamespace(tenant_id="tenant-1"),
        data=SimpleNamespace(conversation_id="conv-1", question="年假有多少天？"),
        pipeline=pipeline,
    )

    # The authoritative response is served unchanged.
    assert response.query_log_id == "q-1"
    assert response.answer == "权威答案"
    assert pipeline.authoritative_calls == 1
    # ...and the shadow replay went through the *shared* execute node, not a copy.
    assert pipeline.execute_plan_calls == 1

    records = sorted(tmp_path.glob("*.json"))
    assert len(records) == 1
    raw = records[0].read_text(encoding="utf-8")
    record = json.loads(raw)

    assert record["schema_version"] == SHADOW_SCHEMA_VERSION
    assert record["identifier"] == "conv-1"
    assert record["tenant_id"] == "tenant-1"

    diff = record["diff"]
    assert diff["match"] is False
    # (a) ChatResponse-derived field digest: the answers differ.
    assert "answer" in diff["field_mismatches"]
    assert diff["fields"]["answer"]["authoritative"] != diff["fields"]["answer"]["shadow"]
    # (b) SSE event sequence: authoritative emitted stage+final, shadow did not.
    events = diff["event_sequence"]
    assert events["match"] is False
    # ``authoritative``/``shadow`` hold *labelled* events (stage name + status) so the
    # diff can tell "one stage missing" from "two stages reordered"; the raw event names
    # stay available as ``authoritative_names``/``shadow_names``.
    assert events["authoritative_names"] == ["stage", "final"]
    assert events["shadow_names"][0] == "stage"
    assert events["authoritative"][0].startswith("stage")
    # Divergence is already at index 0 *by label*: the authoritative stream reports a
    # single ``AnswerAgent`` stage before the final answer, while the shadow run walks the
    # whole graph and opens with ``RouterAgent``. Under bare event names the first entry
    # would look identical ("stage" vs "stage"), which is exactly why the diff compares
    # labels.
    assert events["first_divergence_index"] == 0

    # No answer text is persisted — only lengths and hash prefixes.
    assert "权威答案" not in raw
    assert "影子答案" not in raw
    assert record["authoritative_response_digest"]["answer"]["len"] == len("权威答案")

    shadow_events = [event for event in telemetry.events if event.adapter == "shadow_chat"]
    assert len(shadow_events) == 1
    assert shadow_events[0].mode is AdapterMode.SHADOW
    assert shadow_events[0].parity_match is False
    assert shadow_events[0].tenant_id == "tenant-1"


async def test_shadow_mode_off_runs_nothing_extra(monkeypatch, tmp_path):
    # Default wiring: main.py passes ``shadow=None`` unless GRAPH_SHADOW_MODE is on.
    assert Settings.model_fields["GRAPH_SHADOW_MODE"].default is False

    telemetry = AdapterUsageTelemetry()
    adapter = GraphRouteAdapter(telemetry)
    pipeline = _DivergentPipeline()
    seen: list[object] = []

    async def fake_send_chat_message(session, user, data, pipeline_arg, **deps):
        seen.append(pipeline_arg)
        await pipeline_arg.run(data.question, [], None)
        return SimpleNamespace(query_log_id="q-2", answer="ok")

    monkeypatch.setattr(
        "backend.app.services.chat_service.send_chat_message", fake_send_chat_message
    )

    response = await adapter.chat(
        session=SimpleNamespace(),
        user=SimpleNamespace(tenant_id="tenant-1"),
        data=SimpleNamespace(conversation_id="conv-2", question="年假有多少天？"),
        pipeline=pipeline,
    )

    assert response.query_log_id == "q-2"
    # Identity, not equality: no proxy object wrapped the pipeline.
    assert seen == [pipeline]
    assert pipeline.authoritative_calls == 1
    assert pipeline.execute_plan_calls == 0  # no second graph run
    # Nothing was written anywhere, and telemetry carries route traffic only.
    assert list(tmp_path.iterdir()) == []
    assert [event.adapter for event in telemetry.events] == ["route_chat"]
