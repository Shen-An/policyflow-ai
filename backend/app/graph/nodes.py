"""Typed node contracts for the shared LangGraph (T045).

Each node is a small async function over :class:`GraphState`. Nodes are the only
place work happens; the builder (:mod:`backend.app.graph.builder`) just wires
them and enforces the run-wide budgets. The contract for every node:

- It reads only declared inputs and returns a partial state update — no hidden
  globals, so a checkpoint restore fully determines behavior.
- It appends its name to ``visited`` (via an add-reducer) so the traversal is
  observable and parity-checkable.
- Retry/deadline/tool budgets are enforced structurally, not per-node ad hoc:
  the deadline is checked at every node boundary; the tool budget is checked in
  ``plan_or_tool``; ``finalize`` is a compare-and-set that runs once.

The linear evidence path (validate → … → finalize) is shared by chat, stream
and eval. The file workflow additionally routes through ``sandbox`` and
``approval_interrupt`` before ``generate``.
"""

from __future__ import annotations

import operator
import time
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langgraph.types import interrupt

from backend.app.agents import plan_branch
from backend.app.agents.plan_executor import should_use_plan_executor
from backend.app.agents.plan_normalize import normalize_plan
from backend.app.graph.dependencies import GraphDependencies
from backend.app.graph.evidence_gate import (
    EvidenceCandidate,
    EvidenceGateDecision,
    EvidenceGateInput,
    evaluate_evidence_gate,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime cycle
    from backend.app.agents.pipeline import AgentPipeline
    from backend.app.graph.state import ChatTurnState

__all__ = [
    "GraphNodeError",
    "GraphNodes",
    "GraphState",
    "chat_execute_node",
    "chat_route_node",
    "chat_tot_node",
]


class GraphNodeError(Exception):
    """A typed node failure carrying a stable code and retryability."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class GraphState(TypedDict, total=False):
    # request / identity
    tenant_id: str
    user_id: str
    run_id: str
    conversation_id: str | None
    entrypoint: str
    query: str
    knowledge_version: str
    decision_seed: str
    deterministic: bool
    history: list[dict[str, Any]]
    # run-wide budgets
    deadline_epoch: float | None
    max_tool_calls: int
    tool_calls: int
    # accumulated results
    visited: Annotated[list[str], operator.add]
    rewritten_query: str
    memory_snapshot: dict[str, Any]
    candidates: list[dict[str, Any]]
    accepted_evidence_ids: list[str]
    gate_decision: str
    gate_reason: str
    # file workflow
    requires_approval: bool
    pending_action: dict[str, Any] | None
    approval_decision: str | None
    # output
    answer: str
    evidence: list[str]
    status: str
    finalized: bool


def _check_deadline(state: GraphState) -> None:
    deadline = state.get("deadline_epoch")
    if deadline is not None and time.monotonic() >= deadline:
        raise GraphNodeError(
            "DEADLINE_EXCEEDED", "run deadline exceeded", retryable=False
        )


def _candidates_from_state(state: GraphState) -> list[EvidenceCandidate]:
    return [EvidenceCandidate(**item) for item in state.get("candidates", [])]


class GraphNodes:
    """The graph's node implementations bound to one set of dependencies."""

    def __init__(self, deps: GraphDependencies) -> None:
        self._deps = deps

    async def validate(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        if not state.get("tenant_id") or not state.get("user_id"):
            raise GraphNodeError(
                "VALIDATION_FAILED", "principal identity is required", retryable=False
            )
        if not str(state.get("query", "")).strip():
            raise GraphNodeError(
                "VALIDATION_FAILED", "query is required", retryable=False
            )
        return {"visited": ["validate"], "status": "running"}

    async def memory_load(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        snapshot = await self._deps.load_memory(
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            conversation_id=state.get("conversation_id"),
        )
        return {"visited": ["memory_load"], "memory_snapshot": snapshot}

    async def rewrite(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        rewritten = await self._deps.rewrite_query(
            query=state["query"], history=state.get("history", [])
        )
        return {"visited": ["rewrite"], "rewritten_query": rewritten}

    async def retrieve(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        query = state.get("rewritten_query") or state["query"]
        candidates = await self._deps.retrieve(
            tenant_id=state["tenant_id"],
            query=query,
            knowledge_version=str(state.get("knowledge_version", "unversioned")),
        )
        return {
            "visited": ["retrieve"],
            "candidates": [vars(candidate) for candidate in candidates],
        }

    async def rerank(self, state: GraphState) -> dict[str, Any]:
        # Honest local lexical ordering by relevance — not a cross-encoder.
        _check_deadline(state)
        ranked = sorted(
            state.get("candidates", []),
            key=lambda item: item.get("relevance_score", 0.0),
            reverse=True,
        )
        return {"visited": ["rerank"], "candidates": ranked}

    async def evidence_gate(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        result = evaluate_evidence_gate(
            EvidenceGateInput(
                tenant_id=state["tenant_id"],
                query=state.get("rewritten_query") or state["query"],
                candidates=_candidates_from_state(state),
                retrieval_available=True,
                entrypoint=state.get("entrypoint", "chat"),
            )
        )
        return {
            "visited": ["evidence_gate"],
            "gate_decision": result.decision.value,
            "gate_reason": result.reason,
            "accepted_evidence_ids": list(result.accepted_evidence_ids),
        }

    async def plan_or_tool(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        # A tool request must fit the finite budget. The file workflow marks a
        # pending action requiring approval; chat/eval do not.
        if state.get("requires_approval") and state.get("pending_action"):
            max_calls = int(state.get("max_tool_calls", 0))
            used = int(state.get("tool_calls", 0))
            if used >= max_calls:
                raise GraphNodeError(
                    "TOOL_BUDGET_EXCEEDED",
                    f"tool-call budget exhausted (max {max_calls})",
                    retryable=False,
                )
            return {"visited": ["plan_or_tool"], "tool_calls": used + 1}
        return {"visited": ["plan_or_tool"]}

    async def sandbox(self, state: GraphState) -> dict[str, Any]:
        # File workflow only. Stages the action; never executes a side effect.
        _check_deadline(state)
        return {"visited": ["sandbox"]}

    async def approval_interrupt(self, state: GraphState) -> dict[str, Any]:
        # Human-in-the-loop boundary. interrupt() persists the checkpoint and
        # suspends the run *before* any side effect. Resuming supplies the
        # decision. Nodes before this point must be pure/idempotent.
        decision = interrupt(
            {
                "kind": "approval_required",
                "pending_action": state.get("pending_action"),
                "accepted_evidence_ids": state.get("accepted_evidence_ids", []),
            }
        )
        return {"visited": ["approval_interrupt"], "approval_decision": str(decision)}

    async def generate(self, state: GraphState) -> dict[str, Any]:
        _check_deadline(state)
        accepted_ids = set(state.get("accepted_evidence_ids", []))
        accepted = [
            candidate
            for candidate in _candidates_from_state(state)
            if candidate.evidence_id in accepted_ids
        ]
        if state.get("gate_decision") != EvidenceGateDecision.SUPPORTED.value:
            return {
                "visited": ["generate"],
                "answer": "I don't have reliable policy evidence to answer that.",
                "evidence": [],
            }
        answer = await self._deps.generate(
            query=state.get("rewritten_query") or state["query"],
            accepted_evidence=accepted,
        )
        return {
            "visited": ["generate"],
            "answer": answer,
            "evidence": sorted(accepted_ids),
        }

    async def writeback(self, state: GraphState) -> dict[str, Any]:
        # Idempotent, non-authoritative. Memory writeback is intentionally a
        # no-op in the deterministic path; production deps persist here.
        _check_deadline(state)
        return {"visited": ["writeback"]}

    async def finalize(self, state: GraphState) -> dict[str, Any]:
        # Compare-and-set: only the first finalize sets the terminal state.
        if state.get("finalized"):
            return {}
        succeeded = state.get("gate_decision") is not None
        return {
            "visited": ["finalize"],
            "finalized": True,
            "status": "succeeded" if succeeded else "failed",
        }


# ---------------------------------------------------------------------------
# Chat turn nodes (T053 slice 1)
#
# ``route`` / ``tot`` / ``execute`` are the three stages a chat or eval turn
# walks. They used to live as bound methods on ``AgentPipeline``; T053 lifts the
# *bodies* here so a second driver (the chat-turn state graph in
# :mod:`backend.app.graph.shadow`) can execute the very same code. There is one
# implementation: ``AgentPipeline._pnode_*`` are now one-line delegates to the
# functions below, and ``build_pipeline_graph`` keeps binding the method names it
# always bound.
#
# The pipeline is an explicit first argument (not ``self``) so each function can
# be partially applied into a plain LangGraph node, e.g.
# ``graph.add_node("route", partial(chat_route_node, pipeline))``.
#
# Honest boundary: ``chat_execute_node`` is a thin adapter. The plan → answer →
# compliance stage it runs is still ``AgentPipeline._execute_plan`` (~550 lines
# of real orchestration) and is *called*, not re-implemented here. Moving that
# body out is deliberately out of scope for slice 1; duplicating it would create
# exactly the second divergent copy T053 exists to remove.
# ---------------------------------------------------------------------------


async def chat_route_node(pipeline: AgentPipeline, state: dict[str, Any]) -> dict[str, Any]:
    """Router + plan normalization, or the resume shortcut.

    On a ToT resume (selected steps present) this shortcuts straight to the
    execute node. Otherwise it runs the router, normalizes the plan, emits the
    RouterAgent stage/diagnostics, and decides whether the turn branches into the
    ToT pause node or proceeds directly to execution. The returned ``route_kind``
    (``"tot"`` | ``"execute"``) is the routing decision.
    """
    question = state["question"]
    knowledge_bases = state["knowledge_bases"]
    on_stage = state["on_stage"]
    on_event = state["on_event"]
    turn_state = state["turn_state"]
    l2_enabled = state["l2_enabled"]

    # ---------- Resume path: user-selected ToT option ----------
    selected_plan_steps = state.get("selected_plan_steps")
    selected_router_result = state.get("selected_router_result")
    if selected_plan_steps and selected_router_result is not None:
        router_result = selected_router_result.model_copy(
            update={
                "plan_steps": list(selected_plan_steps),
                "plan_source": "user_selected",
                "difficulty": "branched",
                "reasoning_mode": "tot_select",
                "complexity": "multi_step",
            }
        )
        plan_steps = list(selected_plan_steps)
        multi_step = bool(plan_steps)
        use_l2 = multi_step and should_use_plan_executor(plan_steps, enabled=l2_enabled)
        turn_state.router_result = router_result
        turn_state.plan = plan_steps
        turn_state.reasoning_mode = "tot_select"
        turn_state.plan_options = list(getattr(router_result, "plan_options", None) or [])
        await pipeline._emit_stage(
            on_stage,
            "RouterAgent",
            "success",
            f"ToT 用户选路 · steps={len(plan_steps)} · executor={'L2' if use_l2 else 'L1'}",
        )
        await pipeline._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "RouterAgent",
                        "status": "success",
                        "summary": f"user_selected · {len(plan_steps)} steps · tot_select",
                        "output": router_result.model_dump(mode="json"),
                    }
                ]
            },
        )
        return {
            "router_result": router_result,
            "plan_steps": plan_steps,
            "multi_step": multi_step,
            "use_l2": use_l2,
            "route_kind": "execute",
        }

    await pipeline._emit_stage(on_stage, "RouterAgent", "running", "正在分析问题领域与风险…")
    router_result = await pipeline.router_agent.run(question, knowledge_bases)
    router_result = normalize_plan(
        question,
        router_result,
        enabled=state["planning_enabled"],
        max_steps=state["max_steps"],
        tot_enabled=state["tot_enabled"],
        tot_auto_trigger=state["tot_auto"],
    )
    plan_steps = list(router_result.plan_steps or [])
    multi_step = router_result.complexity == "multi_step" and bool(plan_steps)
    use_l2 = multi_step and should_use_plan_executor(plan_steps, enabled=l2_enabled)
    reasoning_mode = getattr(router_result, "reasoning_mode", "cot_direct") or "cot_direct"
    difficulty = getattr(router_result, "difficulty", "simple") or "simple"
    turn_state.router_result = router_result
    turn_state.plan = plan_steps
    turn_state.reasoning_mode = reasoning_mode  # type: ignore[assignment]

    await pipeline._emit_stage(
        on_stage,
        "RouterAgent",
        "success",
        (
            f"domain={router_result.domain}, task={router_result.task_type}, "
            f"risk={router_result.risk_level}, need_skill={router_result.need_skill}, "
            f"difficulty={difficulty}, mode={reasoning_mode}"
            + (", plan_executor=L2" if use_l2 else "")
        ),
    )
    await pipeline._emit_event(
        on_event,
        "diagnostics_partial",
        {
            "commands": [
                {
                    "name": "RouterAgent",
                    "status": "success",
                    "summary": (
                        f"domain={router_result.domain}, "
                        f"task={router_result.task_type}, risk={router_result.risk_level}, "
                        f"difficulty={difficulty}, mode={reasoning_mode}"
                    ),
                    "output": router_result.model_dump(mode="json"),
                }
            ]
        },
    )

    # Route into the ToT pause node only when the router branched.
    if difficulty == "branched" and reasoning_mode == "tot_select" and state["tot_enabled"]:
        return {
            "router_result": router_result,
            "plan_steps": plan_steps,
            "multi_step": multi_step,
            "use_l2": use_l2,
            "route_kind": "tot",
        }
    return {
        "router_result": router_result,
        "plan_steps": plan_steps,
        "multi_step": multi_step,
        "use_l2": use_l2,
        "route_kind": "execute",
    }


async def chat_tot_node(pipeline: AgentPipeline, state: dict[str, Any]) -> dict[str, Any]:
    """Tree-of-Thought pause: generate options, then pause or auto-pick.

    With ``hitl`` the turn stops before retrieve and returns the awaiting result
    (dual-request protocol). Non-interactive (eval) auto-picks the recommended
    branch and continues to execution. ``route_after_tot`` is the routing
    decision (``"end"`` | ``"execute"``).
    """
    question = state["question"]
    on_stage = state["on_stage"]
    on_event = state["on_event"]
    turn_state = state["turn_state"]
    router_result = state["router_result"]
    hitl = state["hitl"]
    l2_enabled = state["l2_enabled"]

    await pipeline._emit_stage(
        on_stage,
        "PlanBranch",
        "running",
        "生成候选执行路径供选择…",
    )
    llm = getattr(pipeline.router_agent, "llm_service", None)
    plan_options = await plan_branch.generate_plan_options(
        question,
        router_result,
        llm_service=llm,
        min_options=state["tot_min"],
        max_options=state["tot_max"],
        max_steps=state["max_steps"],
    )
    router_result = router_result.model_copy(
        update={
            "plan_options": plan_options,
            "reasoning_mode": "tot_select",
            "difficulty": "branched",
        }
    )
    turn_state.router_result = router_result
    turn_state.plan_options = list(plan_options)
    turn_state.reasoning_mode = "tot_select"
    await pipeline._emit_event(
        on_event,
        "plan_options",
        {
            "difficulty": "branched",
            "reasoning_mode": "tot_select",
            "plan_source": router_result.plan_source,
            "options": [opt.model_dump(mode="json") for opt in plan_options],
            "recommended_option_id": next(
                (opt.id for opt in plan_options if opt.recommended),
                plan_options[0].id if plan_options else None,
            ),
        },
    )
    await pipeline._emit_stage(
        on_stage,
        "PlanBranch",
        "success",
        f"已生成 {len(plan_options)} 条候选路径，等待用户选择",
    )
    await pipeline._emit_event(
        on_event,
        "diagnostics_partial",
        {
            "commands": [
                {
                    "name": "PlanBranch",
                    "status": "success",
                    "summary": f"ToT 选路 · {len(plan_options)} options · awaiting",
                    "output": {
                        "options": [o.model_dump(mode="json") for o in plan_options],
                        "hitl": hitl,
                    },
                }
            ]
        },
    )
    if hitl:
        result = pipeline._awaiting_result(
            router_result, plan_options, turn_state=turn_state, question=question
        )
        return {"router_result": router_result, "result": result, "route_after_tot": "end"}

    # Non-interactive (eval): auto-pick recommended and continue.
    chosen = plan_branch.pick_recommended_option(plan_options)
    if chosen is None:
        turn_state.record_error(
            code="TOT_NO_RECOMMENDED_OPTION",
            message="无推荐路径可自动选用",
            source="PlanBranch",
            severity="error",
        )
        result = pipeline._awaiting_result(
            router_result, plan_options, turn_state=turn_state, question=question
        )
        return {"router_result": router_result, "result": result, "route_after_tot": "end"}
    plan_steps = list(chosen.steps)
    multi_step = bool(plan_steps)
    use_l2 = multi_step and should_use_plan_executor(plan_steps, enabled=l2_enabled)
    router_result = router_result.model_copy(
        update={
            "plan_steps": plan_steps,
            "plan_source": "user_selected",
            "plan_options": plan_options,
        }
    )
    turn_state.router_result = router_result
    turn_state.plan = plan_steps
    await pipeline._emit_stage(
        on_stage,
        "PlanBranch",
        "success",
        f"非交互自动选用推荐路径 {chosen.id}",
    )
    return {
        "router_result": router_result,
        "plan_steps": plan_steps,
        "multi_step": multi_step,
        "use_l2": use_l2,
        "route_after_tot": "execute",
    }


async def chat_execute_node(pipeline: AgentPipeline, state: dict[str, Any]) -> dict[str, Any]:
    """Run the shared plan → answer → compliance stage body.

    Thin adapter over ``AgentPipeline._execute_plan``: the stage itself is not
    duplicated here (see the section note above).
    """
    result = await pipeline._execute_plan(
        state["question"],
        state["knowledge_bases"],
        state["retrieval_request"],
        router_result=state["router_result"],
        plan_steps=state["plan_steps"],
        multi_step=state["multi_step"],
        use_l2=state["use_l2"],
        parallel_enabled=state["parallel_enabled"],
        enable_skill=state["enable_skill"],
        execute_skills=state["execute_skills"],
        working_set=state["working_set"],
        tool_executor=state["tool_executor"],
        on_stage=state["on_stage"],
        on_event=state["on_event"],
        session=state["session"],
        user=state["user"],
        turn_state=state["turn_state"],
        allow_reflection=state["allow_reflection"],
    )
    return {"result": result}
