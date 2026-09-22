"""Chat/Eval orchestration as a real LangGraph (T051–T053, Option A).

`AgentPipeline` no longer *is* the orchestration; it now *drives* one. Its
turn is executed by the single graph this module assembles: ``route`` decides
router/resume, ``tot`` handles the Tree-of-Thought pause/auto-pick branch, and
``execute`` runs the shared plan → answer → compliance stage body. Chat and Eval
both reach this graph through ``AgentPipeline.run`` — one traversal, one place
where the turn's control flow lives.

Honesty boundary (recorded in docs/08 §10): this in-process orchestration graph
is a *distinct* StateGraph from the durable evidence-path graph in
``backend.app.graph.builder``. The two share the fail-closed evidence-gate
*semantics* (no reliable evidence ⇒ safe refusal, enforced in the answer/
compliance stage the ``execute`` node calls), not a single node vocabulary. It
carries live Python objects (the agents, the ``TurnState`` blackboard, the SSE
``on_stage``/``on_event`` callbacks) through state and runs without a
checkpointer, because a chat turn is resumed by the dual-request ToT protocol at
the service layer, not by a checkpoint restore. The durable, checkpoint-backed
graph remains the file-workflow / authorized-run surface.

The node *bodies* are the exact stage logic relocated from the former
``AgentPipeline._run_impl``; nothing was rewritten. That is what lets the full
legacy contract suite (PipelineResult shape, ToT pause, reflection, compliance
release gate, off-topic drop, diagnostics/event emission) stay green.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

if TYPE_CHECKING:
    from backend.app.agents.pipeline import AgentPipeline

__all__ = ["PipelineGraphState", "build_pipeline_graph"]


class PipelineGraphState(TypedDict, total=False):
    """Per-turn state threaded through the orchestration graph.

    Holds the original ``run`` call arguments, the settings-derived flags
    computed once at turn start, the evolving router/plan decision, and the
    final :class:`PipelineResult`. Live objects (agents live on the pipeline;
    the blackboard, callbacks, tool executor and budget live here) pass through
    untouched — this graph is never checkpointed.
    """

    # -- original run() arguments --
    question: str
    knowledge_bases: list[Any]
    retrieval_request: Any | None
    enable_skill: bool
    working_set: Any | None
    on_stage: Any | None
    on_event: Any | None
    session: Any | None
    user: Any | None
    tool_executor: Any | None
    execute_skills: bool
    selected_plan_steps: list[Any] | None
    selected_router_result: Any | None
    hitl: bool
    allow_reflection: bool
    turn_state: Any
    budget: Any

    # -- settings-derived flags (computed once in run()/_run_impl) --
    planning_enabled: bool
    max_steps: int
    tot_enabled: bool
    tot_auto: bool
    tot_min: int
    tot_max: int
    l2_enabled: bool
    parallel_enabled: bool

    # -- evolving decision --
    router_result: Any
    plan_steps: list[Any]
    multi_step: bool
    use_l2: bool
    route_kind: str  # "tot" | "execute"
    route_after_tot: str  # "end" | "execute"

    # -- output --
    result: Any  # PipelineResult


def _route_after_route(state: PipelineGraphState) -> str:
    return "tot" if state.get("route_kind") == "tot" else "execute"


def _route_after_tot(state: PipelineGraphState) -> str:
    return "end" if state.get("route_after_tot") == "end" else "execute"


def build_pipeline_graph(pipeline: AgentPipeline) -> CompiledStateGraph:
    """Assemble the chat/eval orchestration graph bound to one pipeline.

    Nodes are bound methods on ``pipeline`` so they reach the injected agents,
    settings and SSE emit helpers. No checkpointer: a chat turn resumes via the
    dual-request ToT protocol at the service layer, not a checkpoint restore.
    """
    graph: StateGraph = StateGraph(PipelineGraphState)

    graph.add_node("route", pipeline._pnode_route)
    graph.add_node("tot", pipeline._pnode_tot)
    graph.add_node("execute", pipeline._pnode_execute)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route", _route_after_route, {"tot": "tot", "execute": "execute"}
    )
    graph.add_conditional_edges(
        "tot", _route_after_tot, {"end": END, "execute": "execute"}
    )
    graph.add_edge("execute", END)

    return graph.compile()
