"""T053 slice 1 — shadow comparison of the chat turn against a second driver.

背景
====
Chat / Eval 现在的真实控制流在 ``backend.app.graph.pipeline_graph`` 里（进程内
``StateGraph``，节点体已抽到 ``backend.app.graph.nodes`` 的 chat_route_node /
chat_tot_node / chat_execute_node）。T053 的目标是把这条编排收进持久图，但**在
diff 归零之前不能切流量**。

这个模块只做一件事：影子模式。开启后，同一轮对话会被**再跑一遍**——用同一个
``build_pipeline_graph`` 构造器、同一批节点函数，只是换了 driver 标签
（``node_prefix="shadow_"``）并且把决策记录进 ``ChatTurnState``——然后把两次
结果做结构化 diff，写进 ``artifacts/graph/shadow/*.json`` 并发一条
``shadow_chat`` 遥测事件。

诚实的边界（别把这里读过头）
============================
1. **权威路径不变**。被服务出去的那次 run 是原来那次 ``AgentPipeline.run``——
   本模块只是包了一层记录代理，把 ``on_stage`` / ``on_event`` 回调 tee 一份，
   再把同一个返回值原样返回。既不换控制流，也不改 API 形状。
2. **影子跑不写库**。默认 ``disable_side_effects=True``：``session`` / ``user`` /
   ``tool_executor`` 传 None、``execute_skills`` 强制 False，以免同一轮把
   记忆 writeback、query_log、工具副作用都做两遍。代价是：影子跑**无法**复现
   依赖 DB 会话的部分，所以 diff 的语义是"编排决策是否一致"，不是"落库结果是否
   一致"。真要连副作用一起比对，得先有事务级的沙箱（本切片没有）。
3. **diff 的对象是 PipelineResult 投影，不是 ChatResponse 本体**。影子 driver 到
   ``PipelineResult`` 就停了——它不构造 ``ChatResponse``（那是
   ``chat_service`` 的职责，复制一遍就是第二份实现）。因此字段 diff 用的是两侧
   共用的 PipelineResult 投影 ``_result_digest``；权威侧真实 ``ChatResponse`` 的
   字段摘要另外原样记在 ``authoritative_response_digest`` 里供人工核对。
4. **答案正文不落盘**。文本字段只记录长度与 sha256 前缀；不含问题、答案、证据
   正文。``ChatResponse`` 的 JSON 字段逐个做"长度 / 哈希 / 元素数"摘要。
5. **影子跑的任何异常都被吞掉**：记 ``shadow.error`` 后照常返回权威响应。开关默认
   ``False``（``GRAPH_SHADOW_MODE``），关闭时连代理对象都不会创建。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from backend.app.graph.legacy_adapter import AdapterMode, AdapterTelemetryEvent
from backend.app.graph.state import ChatTurnState, _event_summary

__all__ = ["SHADOW_SCHEMA_VERSION", "ChatShadowRunner", "ShadowRecording"]

LOGGER = logging.getLogger(__name__)

SHADOW_SCHEMA_VERSION = "ChatShadowDiff@1"

#: Where the diff records land (relative to the process working directory).
DEFAULT_SHADOW_DIR = Path("artifacts") / "graph" / "shadow"

#: Positional prefix of ``AgentPipeline.run``; everything else arrives as a kwarg.
_RUN_POSITIONAL = (
    "question",
    "knowledge_bases",
    "retrieval_request",
    "enable_skill",
    "working_set",
    "on_stage",
    "on_event",
)


def _fingerprint(text: Any) -> dict[str, Any] | None:
    """Content-free fingerprint of a text field (length + sha256 prefix)."""

    if not isinstance(text, str) or not text:
        return None
    return {"len": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}


def _result_digest(result: Any) -> dict[str, Any]:
    """Structural digest of a ``PipelineResult`` — both drivers use this shape.

    Deliberately duck-typed: the authoritative call returns the real
    ``PipelineResult``, while a test double may return something simpler. Any
    missing attribute degrades to ``None`` instead of raising, so a divergence
    shows up as a diff rather than an exception.
    """

    if result is None:
        return {}

    router = getattr(result, "router_result", None)
    answer = getattr(result, "answer_result", None)
    compliance = getattr(result, "compliance", None)
    retrieval = getattr(result, "retrieval_result", None)
    plan = list(getattr(result, "plan", None) or [])
    skill_results = list(getattr(result, "skill_results", None) or [])
    suggested = list(getattr(result, "suggested_skills", None) or [])
    errors = list(getattr(result, "errors", None) or [])

    return {
        "status": getattr(result, "status", None),
        "reasoning_mode": getattr(result, "reasoning_mode", None),
        "router": {
            "domain": getattr(router, "domain", None),
            "task_type": getattr(router, "task_type", None),
            "risk_level": getattr(router, "risk_level", None),
            "need_skill": getattr(router, "need_skill", None),
            "difficulty": getattr(router, "difficulty", None),
            "plan_source": getattr(router, "plan_source", None),
            "plan_steps": [
                getattr(step, "title", None) or getattr(step, "id", None) for step in plan
            ],
            "plan_options": len(list(getattr(router, "plan_options", None) or [])),
        },
        "retrieval": {
            "evidence_count": len(list(getattr(retrieval, "evidence", None) or [])),
        },
        "answer": {
            "text": _fingerprint(getattr(answer, "answer", None)),
            "confidence": getattr(answer, "confidence_score", None),
            "tool_trace": len(list(getattr(answer, "tool_trace", None) or [])),
        },
        "skills": {
            "results": len(skill_results),
            "suggested": sorted(
                str(getattr(item, "name", item)) for item in suggested
            ),
        },
        "compliance": {
            "verdict": getattr(compliance, "verdict", None)
            or getattr(compliance, "status", None),
        },
        "errors": [str(getattr(err, "code", err)) for err in errors],
    }


def _response_digest(response: Any) -> dict[str, Any] | None:
    """Field-by-field digest of the authoritative ``ChatResponse`` (no content)."""

    if response is None:
        return None
    if hasattr(response, "model_dump"):
        payload = response.model_dump(mode="json")
    elif isinstance(response, dict):
        payload = response
    else:
        return {"type": type(response).__name__}
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}

    digest: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            digest[key] = {"len": len(value), "sha256": _fingerprint(value), "empty": not value}
        elif isinstance(value, (list, tuple)):
            digest[key] = {"len": len(value)}
        elif isinstance(value, dict):
            digest[key] = {"len": len(value)}
        elif isinstance(value, (int, float, bool)) or value is None:
            digest[key] = value
        else:
            digest[key] = {"type": type(value).__name__}
    return digest


def _bind_run_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    bound = dict(kwargs)
    for name, value in zip(_RUN_POSITIONAL, args):
        bound.setdefault(name, value)
    return bound


class _ShadowPipelineShim:
    """Replays the pipeline's node surface while recording into a ChatTurnState.

    ``build_pipeline_graph`` binds nodes by attribute name, so this object only
    has to expose the three ``_pnode_*`` attributes. The bodies themselves are
    the shared functions in ``backend.app.graph.nodes`` — nothing is re-implemented
    here, this is pure observation.
    """

    def __init__(self, pipeline: Any, turn: ChatTurnState) -> None:
        self._pipeline = pipeline
        self._turn = turn

    async def _pnode_route(self, state: dict[str, Any]) -> dict[str, Any]:
        from backend.app.graph.nodes import chat_route_node

        update = await chat_route_node(self._pipeline, state)
        self._turn.record_node("route")
        self._turn.question = str(state.get("question") or self._turn.question)
        self._turn.router_result = update.get("router_result")
        self._turn.plan_steps = list(update.get("plan_steps") or [])
        self._turn.route_kind = str(update.get("route_kind") or "")
        return update

    async def _pnode_tot(self, state: dict[str, Any]) -> dict[str, Any]:
        from backend.app.graph.nodes import chat_tot_node

        update = await chat_tot_node(self._pipeline, state)
        self._turn.record_node("tot")
        router_result = update.get("router_result") or self._turn.router_result
        self._turn.router_result = router_result
        self._turn.plan_options = list(getattr(router_result, "plan_options", None) or [])
        return update

    async def _pnode_execute(self, state: dict[str, Any]) -> dict[str, Any]:
        from backend.app.graph.nodes import chat_execute_node

        update = await chat_execute_node(self._pipeline, state)
        self._turn.record_node("execute")
        self._turn.result = update.get("result")
        self._turn.compliance = getattr(self._turn.result, "compliance", None)
        for item in list(getattr(self._turn.result, "suggested_skills", None) or []):
            self._turn.record_skill_hit(str(getattr(item, "name", item)), "suggested")
        return update


class ShadowRecording:
    """State captured from one authoritative run, plus the ``finish`` hook."""

    def __init__(
        self,
        runner: ChatShadowRunner,
        pipeline: Any,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> None:
        self._runner = runner
        self.pipeline = pipeline
        self.tenant_id = tenant_id or ""
        self.conversation_id = conversation_id or ""
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        # Everything but ``run`` behaves exactly like the wrapped pipeline.
        return getattr(self.pipeline, name)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        captured = _bind_run_args(args, kwargs)
        events: list[dict[str, Any]] = []
        user_on_stage = captured.get("on_stage")
        user_on_event = captured.get("on_event")

        async def _on_stage(stage: str, status: str, message: str) -> None:
            events.append({"event": "stage", "stage": stage, "status": status})
            if user_on_stage is not None:
                await user_on_stage(stage, status, message)

        async def _on_event(event: str, payload: Any) -> None:
            events.append({"event": event, "payload": _event_summary(payload)})
            if user_on_event is not None:
                await user_on_event(event, payload)

        passthrough = dict(kwargs)
        passthrough["on_stage"] = _on_stage
        passthrough["on_event"] = _on_event

        result = await self.pipeline.run(*args, **passthrough)
        self.calls.append({**captured, "events": events, "result": result})
        return result


class ChatShadowRunner:
    """Runs the shadow comparison for one chat turn; never affects the response."""

    def __init__(
        self,
        *,
        telemetry: Any = None,
        output_dir: Path | str | None = None,
        disable_side_effects: bool = True,
    ) -> None:
        self._telemetry = telemetry
        self._output_dir = Path(output_dir) if output_dir is not None else DEFAULT_SHADOW_DIR
        self._disable_side_effects = disable_side_effects

    # -- wiring ----------------------------------------------------------

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def wrap(
        self,
        pipeline: Any,
        *,
        tenant_id: str = "",
        conversation_id: str = "",
    ) -> ShadowRecording:
        return ShadowRecording(
            self, pipeline, tenant_id=tenant_id, conversation_id=conversation_id
        )

    # -- shadow run ------------------------------------------------------

    async def _run_shadow(self, recording: ShadowRecording) -> dict[str, Any]:
        """Replay the last captured run through the shared node functions."""

        from backend.app.agents.base import TurnState
        from backend.app.graph.pipeline_graph import build_pipeline_graph

        call = recording.calls[-1]
        pipeline = recording.pipeline
        question = str(call.get("question") or "")
        chat_turn = ChatTurnState(question=question)
        shadow_events: list[dict[str, Any]] = []

        async def _on_stage(stage: str, status: str, message: str) -> None:
            shadow_events.append({"event": "stage", "stage": stage, "status": status})
            chat_turn.record_stage(stage, status, message)

        async def _on_event(event: str, payload: Any) -> None:
            shadow_events.append({"event": event, "payload": _event_summary(payload)})
            chat_turn.record_event(event, payload)

        flags_fn = getattr(pipeline, "_graph_flags", None)
        flags = flags_fn() if callable(flags_fn) else {}

        side_effects = not self._disable_side_effects
        state = {
            "question": question,
            "knowledge_bases": list(call.get("knowledge_bases") or []),
            "retrieval_request": call.get("retrieval_request"),
            "enable_skill": bool(call.get("enable_skill", True)),
            "working_set": call.get("working_set"),
            "on_stage": _on_stage,
            "on_event": _on_event,
            "session": call.get("session") if side_effects else None,
            "user": call.get("user") if side_effects else None,
            "tool_executor": call.get("tool_executor") if side_effects else None,
            "execute_skills": (
                bool(call.get("execute_skills", False)) if side_effects else False
            ),
            "selected_plan_steps": call.get("selected_plan_steps"),
            "selected_router_result": call.get("selected_router_result"),
            "hitl": bool(call.get("hitl", True)),
            "allow_reflection": bool(call.get("allow_reflection", True)),
            "turn_state": TurnState(question=question, status="running"),
            "budget": None,
            **flags,
        }

        shim = _ShadowPipelineShim(pipeline, chat_turn)
        graph = build_pipeline_graph(shim, node_prefix="shadow_")
        final_state = await graph.ainvoke(state)
        return {
            "ok": True,
            "result": final_state.get("result"),
            "events": shadow_events,
            "chat_turn": chat_turn,
            "side_effects_disabled": self._disable_side_effects,
        }

    # -- diff + record ---------------------------------------------------

    def _build_record(
        self,
        recording: ShadowRecording,
        shadow: dict[str, Any],
        *,
        response: Any,
    ) -> dict[str, Any]:
        call = recording.calls[-1]
        authoritative_result = call.get("result")
        authoritative_events = list(call.get("events") or [])
        shadow_events = list(shadow.get("events") or [])
        chat_turn: ChatTurnState | None = shadow.get("chat_turn")

        auth_digest = _result_digest(authoritative_result)
        shadow_digest = _result_digest(shadow.get("result"))
        field_diff: dict[str, Any] = {}
        for key in sorted(set(auth_digest) | set(shadow_digest)):
            left = auth_digest.get(key)
            right = shadow_digest.get(key)
            field_diff[key] = {
                "authoritative": left,
                "shadow": right,
                "match": left == right,
            }
        mismatch = [key for key, item in field_diff.items() if not item["match"]]

        auth_names = [item["event"] for item in authoritative_events]
        shadow_names = [item["event"] for item in shadow_events]
        first_divergence: int | None = None
        for index, (left, right) in enumerate(zip(auth_names, shadow_names)):
            if left != right:
                first_divergence = index
                break
        if first_divergence is None and len(auth_names) != len(shadow_names):
            first_divergence = min(len(auth_names), len(shadow_names))

        events_match = auth_names == shadow_names
        match = bool(shadow.get("ok")) and not mismatch and events_match

        identifier = (recording.conversation_id or "").strip() or uuid.uuid4().hex[:12]
        return {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "captured_at": datetime.now(UTC).isoformat(),
            "identifier": identifier,
            "tenant_id": recording.tenant_id,
            "conversation_id": recording.conversation_id,
            "driver": {
                "authoritative": "pipeline_graph",
                "shadow": "pipeline_graph(node_prefix=shadow_)",
                "side_effects_disabled": shadow.get("side_effects_disabled", True),
            },
            "question": _fingerprint(call.get("question") or ""),
            "authoritative_response_digest": _response_digest(response),
            "diff": {
                "match": match,
                "field_mismatches": mismatch,
                "fields": field_diff,
                "event_sequence": {
                    "match": events_match,
                    "authoritative": auth_names,
                    "shadow": shadow_names,
                    "first_divergence_index": first_divergence,
                },
            },
            "shadow": {
                "ok": bool(shadow.get("ok")),
                "error": shadow.get("error"),
                "chat_turn": chat_turn.summary() if chat_turn is not None else None,
            },
        }

    def _write_record(self, record: dict[str, Any]) -> str | None:
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            path = self._output_dir / f"{stamp}-{record['identifier']}.json"
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return str(path)
        except Exception:  # pragma: no cover - filesystem guards
            LOGGER.warning("shadow diff record could not be written", exc_info=True)
            return None

    def _record_telemetry(self, record: dict[str, Any]) -> None:
        if self._telemetry is None:
            return
        try:
            self._telemetry.record(
                AdapterTelemetryEvent(
                    adapter="shadow_chat",
                    mode=AdapterMode.SHADOW,
                    tenant_id=str(record.get("tenant_id") or ""),
                    run_id=str(record.get("identifier") or ""),
                    parity_match=bool(record["diff"]["match"]),
                )
            )
        except Exception:  # pragma: no cover - telemetry must never break chat
            LOGGER.warning("shadow telemetry could not be recorded", exc_info=True)

    # -- public entry point ----------------------------------------------

    async def finish(self, recording: ShadowRecording, *, response: Any = None) -> None:
        """Run the shadow graph for the captured turn, then diff + persist.

        Swallows every failure: the authoritative response has already been
        produced by the time this is called, so shadow problems must not surface.
        """

        if not recording.calls:
            return  # nothing ran (e.g. the authoritative call raised) — nothing to compare
        try:
            try:
                shadow = await self._run_shadow(recording)
            except Exception as exc:  # noqa: BLE001 - shadow failures are inert
                LOGGER.warning("shadow graph run failed", exc_info=True)
                shadow = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "events": [],
                    "chat_turn": None,
                }
            record = self._build_record(recording, shadow, response=response)
            record["record_path"] = None
            path = self._write_record(record)
            record["record_path"] = path
            self._record_telemetry(record)
        except Exception:  # noqa: BLE001 - never propagate into the chat path
            LOGGER.warning("shadow comparison failed", exc_info=True)


ShadowFinish = Callable[..., Awaitable[None]]
