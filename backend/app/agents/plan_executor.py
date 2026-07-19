"""L2 PlanExecutor: true per-step execution with dependency waves + optional parallel.

Still a centralized service (not peer multi-agent). Parallelism is only for
independent ready steps (typically multiple retrieve / independent skills).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlmodel import Session

from backend.app.agents.base import TurnError, TurnState
from backend.app.agents.grounding import question_evidence_support
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.db.models import KnowledgeBase, User
from backend.app.schemas.chat import PlanStep, RouterResult
from backend.app.schemas.retrieval import Evidence, RetrievalRequest, RetrievalResult

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]
StageCallback = Callable[[str, str, str], Awaitable[None] | None]
StepStatus = Literal["pending", "running", "success", "skipped", "error"]

# Kinds that may share a parallel wave when mutually independent.
_PARALLELIZABLE = {"retrieve", "skill"}
# Kinds that always serialize (finalization / side-effecting / user-facing).
_SERIAL_KINDS = {"answer", "tool", "verify"}


@dataclass
class PlanExecutorResult:
    plan_steps: list[PlanStep]
    evidence: list[Evidence]
    retrieval_result: RetrievalResult
    skill_results: list[dict[str, Any]] = field(default_factory=list)
    suggested_skills: list[dict[str, str]] = field(default_factory=list)
    waves: list[list[str]] = field(default_factory=list)
    parallel_used: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[TurnError] = field(default_factory=list)


def _evidence_key(item: Evidence) -> str:
    if item.chunk_id:
        return f"chunk:{item.chunk_id}"
    if item.document_id and item.snippet:
        digest = hashlib.sha1(item.snippet.strip().encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"doc:{item.document_id}:{digest}"
    digest = hashlib.sha1(
        f"{item.knowledge_base_id}|{item.snippet}".encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    return f"snip:{digest}"


def merge_evidence(existing: list[Evidence], incoming: list[Evidence]) -> list[Evidence]:
    """Dedup evidence bag and renumber ranks."""
    seen: set[str] = set()
    merged: list[Evidence] = []
    for item in list(existing) + list(incoming):
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    renumbered: list[Evidence] = []
    for idx, item in enumerate(merged, start=1):
        if item.rank != idx:
            renumbered.append(item.model_copy(update={"rank": idx}))
        else:
            renumbered.append(item)
    return renumbered


def infer_dependencies(steps: list[PlanStep]) -> list[PlanStep]:
    """
    Fill depends_on when empty.

    Rules (conservative):
    - Preserve explicit depends_on.
    - answer / verify depend on all prior non-answer/verify steps (or previous step).
    - skill / tool depend on all prior retrieve steps (need evidence); if none, prior step.
    - consecutive retrieve with different queries: no cross-deps → parallelizable.
    - consecutive retrieve with same/empty query: later depends on earlier (dedupe serial).
    """
    by_id = {s.id: s for s in steps}
    out: list[PlanStep] = []
    prior_ids: list[str] = []
    prior_retrieve_ids: list[str] = []
    prior_retrieve_queries: dict[str, str] = {}

    for step in steps:
        deps = [d for d in (step.depends_on or []) if d in by_id and d != step.id]
        if not deps:
            if step.kind in {"answer", "verify"}:
                # Prefer all prior productive steps; fallback to last prior.
                productive = [
                    sid
                    for sid in prior_ids
                    if by_id[sid].kind in {"retrieve", "skill", "tool"}
                ]
                deps = productive or (prior_ids[-1:] if prior_ids else [])
            elif step.kind in {"skill", "tool"}:
                if prior_retrieve_ids:
                    deps = list(prior_retrieve_ids)
                elif prior_ids:
                    deps = [prior_ids[-1]]
            elif step.kind == "retrieve":
                q = (step.query or "").strip().lower()
                # Same query as a prior retrieve → serialize onto that one.
                for rid, rq in prior_retrieve_queries.items():
                    if q and rq == q:
                        deps = [rid]
                        break
                # empty query after another retrieve: treat as continuation → depend last retrieve
                if not deps and not q and prior_retrieve_ids:
                    deps = [prior_retrieve_ids[-1]]
            # else: first steps / independent retrieve → no deps
        out.append(step.model_copy(update={"depends_on": deps}))
        prior_ids.append(step.id)
        if step.kind == "retrieve":
            prior_retrieve_ids.append(step.id)
            prior_retrieve_queries[step.id] = (step.query or "").strip().lower()
    return out


def build_execution_waves(
    steps: list[PlanStep],
    *,
    parallel_enabled: bool = True,
) -> list[list[str]]:
    """
    Kahn-style waves: each wave = steps whose deps are all in previous waves.

    When parallel_enabled is False, each ready step becomes its own wave (serial).
    answer/tool/verify never share a wave with anything else.
    """
    remaining = {s.id: s for s in steps}
    completed: set[str] = set()
    waves: list[list[str]] = []
    safety = 0
    while remaining and safety < len(steps) + 5:
        safety += 1
        ready = [
            sid
            for sid, step in remaining.items()
            if all(dep in completed for dep in (step.depends_on or []))
        ]
        if not ready:
            # Cycle / missing dep — flush remaining serially to avoid deadlock.
            for sid in list(remaining.keys()):
                waves.append([sid])
                completed.add(sid)
                remaining.pop(sid, None)
            break

        # Preserve original order among ready steps.
        order = {s.id: i for i, s in enumerate(steps)}
        ready.sort(key=lambda sid: order.get(sid, 0))

        if not parallel_enabled:
            for sid in ready:
                waves.append([sid])
                completed.add(sid)
                remaining.pop(sid, None)
            continue

        # Split serial kinds into singleton waves; parallelizable kinds may batch.
        wave_batch: list[str] = []
        for sid in ready:
            kind = remaining[sid].kind
            if kind in _SERIAL_KINDS:
                if wave_batch:
                    waves.append(wave_batch)
                    for x in wave_batch:
                        completed.add(x)
                        remaining.pop(x, None)
                    wave_batch = []
                waves.append([sid])
                completed.add(sid)
                remaining.pop(sid, None)
            elif kind in _PARALLELIZABLE:
                wave_batch.append(sid)
            else:
                if wave_batch:
                    waves.append(wave_batch)
                    for x in wave_batch:
                        completed.add(x)
                        remaining.pop(x, None)
                    wave_batch = []
                waves.append([sid])
                completed.add(sid)
                remaining.pop(sid, None)
        if wave_batch:
            waves.append(wave_batch)
            for x in wave_batch:
                completed.add(x)
                remaining.pop(x, None)
    return waves


def should_use_plan_executor(plan_steps: list[PlanStep], *, enabled: bool) -> bool:
    """L2 only when plan has real multi work (multi retrieve/skill or user multi-step)."""
    if not enabled or not plan_steps:
        return False
    retrieve_n = sum(1 for s in plan_steps if s.kind == "retrieve")
    skill_n = sum(1 for s in plan_steps if s.kind == "skill")
    if retrieve_n >= 2 or skill_n >= 2:
        return True
    # Single retrieve + skill + answer still benefits from true step status, cheap.
    if retrieve_n >= 1 and skill_n >= 1:
        return True
    # User-style multi productive steps without answer-only noise.
    productive = [s for s in plan_steps if s.kind not in {"answer", "verify"}]
    return len(productive) >= 2


class PlanExecutor:
    """Deterministic step runner with optional parallel waves."""

    def __init__(
        self,
        retrieval_agent: RetrievalAgent,
        skill_agent: SkillAgent,
        *,
        parallel_enabled: bool = True,
    ) -> None:
        self.retrieval_agent = retrieval_agent
        self.skill_agent = skill_agent
        self.parallel_enabled = parallel_enabled

    async def _emit_event(
        self,
        on_event: EventCallback | None,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        if on_event is None:
            return
        result = on_event(event, payload)
        if result is not None:
            await result

    async def _emit_stage(
        self,
        on_stage: StageCallback | None,
        stage: str,
        status: str,
        message: str,
    ) -> None:
        if on_stage is None:
            return
        result = on_stage(stage, status, message)
        if result is not None:
            await result

    async def _set_step(
        self,
        steps: list[PlanStep],
        step_id: str,
        status: StepStatus,
        message: str,
        on_event: EventCallback | None,
    ) -> list[PlanStep]:
        updated: list[PlanStep] = []
        for step in steps:
            if step.id == step_id:
                updated.append(step.model_copy(update={"status": status}))
            else:
                updated.append(step)
        await self._emit_event(
            on_event,
            "plan_step",
            {"id": step_id, "status": status, "message": message},
        )
        return updated

    async def run(
        self,
        question: str,
        plan_steps: list[PlanStep],
        router_result: RouterResult,
        retrieval_request: RetrievalRequest | None,
        knowledge_bases: list[KnowledgeBase],
        *,
        enable_skill: bool = True,
        execute_skills: bool = False,
        session: Session | None = None,
        user: User | None = None,
        on_stage: StageCallback | None = None,
        on_event: EventCallback | None = None,
        fallback_query: str = "",
        turn_state: TurnState | None = None,
    ) -> PlanExecutorResult:
        steps = infer_dependencies(list(plan_steps))
        waves = build_execution_waves(steps, parallel_enabled=self.parallel_enabled)
        parallel_used = any(len(w) > 1 for w in waves)

        evidence_bag: list[Evidence] = []
        traces: list[Any] = []
        warnings: list[str] = list()
        skill_results: list[dict[str, Any]] = []
        suggested_skills: list[dict[str, str]] = []
        total_latency = 0
        rerank_applied = False
        status_by_id: dict[str, StepStatus] = {s.id: s.status for s in steps}
        errors: list[TurnError] = []
        kind_by_id = {s.id: s.kind for s in steps}

        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "PlanExecutor",
                        "status": "running",
                        "summary": (
                            f"L2 waves={len(waves)} parallel="
                            f"{'yes' if parallel_used else 'no'}"
                        ),
                        "output": {
                            "waves": waves,
                            "parallel_enabled": self.parallel_enabled,
                            "parallel_used": parallel_used,
                            "steps": [s.model_dump(mode="json") for s in steps],
                        },
                    }
                ]
            },
        )

        # Emit initial plan with inferred depends_on for UI.
        await self._emit_event(
            on_event,
            "plan",
            {
                "complexity": router_result.complexity,
                "plan_source": router_result.plan_source,
                "steps": [s.model_dump(mode="json") for s in steps],
                "waves": waves,
                "parallel_used": parallel_used,
            },
        )

        async def run_one(step: PlanStep) -> tuple[str, StepStatus, str, dict[str, Any]]:
            """Execute one step; returns (id, status, message, side_effects)."""
            side: dict[str, Any] = {
                "evidence": [],
                "trace": [],
                "skill_results": [],
                "suggested": [],
                "latency_ms": 0,
                "rerank_applied": False,
                "warnings": [],
            }
            if step.kind == "retrieve":
                if retrieval_request is None or not knowledge_bases:
                    return step.id, "error", "无可用知识库检索", side
                query = (
                    (step.query or "").strip()
                    or (router_result.rewrite_query or "").strip()
                    or fallback_query
                    or question
                )
                req = retrieval_request.model_copy(update={"query": query[:4000]})
                result = await self.retrieval_agent.run(req)
                side["latency_ms"] = result.latency_ms
                side["rerank_applied"] = result.rerank_applied
                side["trace"] = list(result.trace)
                evidence = list(result.evidence)
                support = question_evidence_support(question, evidence)
                if evidence and not support["supported"]:
                    side["warnings"].append("OFF_TOPIC_RETRIEVAL_DROPPED")
                    evidence = []
                side["evidence"] = evidence
                count = len(evidence)
                if count:
                    return step.id, "success", f"命中 {count} 条（q={query[:40]}）", side
                return step.id, "error", f"未命中可靠证据（q={query[:40]}）", side

            if step.kind == "skill":
                if not enable_skill:
                    return step.id, "skipped", "Skill 未启用", side
                skill_name = (step.skill_hint or "").strip()
                if not skill_name:
                    # Fall back to router task mapping via suggest.
                    hints = self.skill_agent.suggest(
                        step.query or question,
                        router_result.model_copy(update={"need_skill": True}),
                        enabled=True,
                    )
                    skill_name = hints[0]["name"] if hints else ""
                    side["suggested"] = hints
                else:
                    side["suggested"] = [
                        {"name": skill_name, "description": skill_name}
                    ]
                if not skill_name:
                    return step.id, "skipped", "无匹配 Skill", side
                if not execute_skills or session is None or user is None:
                    return step.id, "success", f"建议 Skill {skill_name}", side
                if not evidence_bag:
                    return step.id, "error", "无证据，跳过编造清单", side
                one = await self.skill_agent.execute_one(
                    session,
                    user,
                    skill_name,
                    step.query or question,
                    evidence_bag,
                    step_id=step.id,
                )
                side["skill_results"] = [one]
                if one.get("status") == "success":
                    return step.id, "success", f"Skill {skill_name} 完成", side
                if one.get("status") == "skipped":
                    return step.id, "skipped", str(one.get("error") or "skipped"), side
                return step.id, "error", str(one.get("error") or "skill failed"), side

            if step.kind == "tool":
                # L2: tool steps are markers; real tools run in Answer loop.
                return step.id, "skipped", "工具由 Answer tool-loop 执行", side

            if step.kind == "verify":
                if not evidence_bag:
                    return step.id, "error", "校验：无可靠证据", side
                return step.id, "success", f"校验：证据 {len(evidence_bag)} 条", side

            if step.kind == "answer":
                # Final answer is produced by AnswerAgent after executor.
                return step.id, "success", "进入最终回答", side

            return step.id, "skipped", f"未知 kind={step.kind}", side

        for wave in waves:
            wave_steps = [s for s in steps if s.id in wave]
            # Mark running
            for step in wave_steps:
                steps = await self._set_step(
                    steps, step.id, "running", f"执行：{step.title}", on_event
                )
                status_by_id[step.id] = "running"

            if len(wave_steps) == 1:
                outcomes = [await run_one(wave_steps[0])]
            else:
                await self._emit_stage(
                    on_stage,
                    "PlanExecutor",
                    "running",
                    f"并行执行 {len(wave_steps)} 步："
                    + "、".join(s.id for s in wave_steps),
                )
                outcomes = list(await asyncio.gather(*[run_one(s) for s in wave_steps]))

            # Apply side effects deterministically in plan order.
            outcomes_by_id = {item[0]: item for item in outcomes}
            for step in wave_steps:
                sid, st, msg, side = outcomes_by_id[step.id]
                if side.get("evidence"):
                    evidence_bag = merge_evidence(evidence_bag, side["evidence"])
                if side.get("trace"):
                    traces.extend(side["trace"])
                if side.get("skill_results"):
                    skill_results.extend(side["skill_results"])
                if side.get("suggested"):
                    for item in side["suggested"]:
                        if item not in suggested_skills:
                            suggested_skills.append(item)
                total_latency += int(side.get("latency_ms") or 0)
                rerank_applied = rerank_applied or bool(side.get("rerank_applied"))
                for w in side.get("warnings") or []:
                    warnings.append(w)
                    if turn_state is not None:
                        turn_state.record_error(
                            code=str(w),
                            message=str(w),
                            source="PlanExecutor",
                            step_id=sid,
                            severity="warning",
                        )
                steps = await self._set_step(steps, sid, st, msg, on_event)
                status_by_id[sid] = st
                # Centralized error ledger: non-success step outcomes always recorded.
                if st != "success":
                    if turn_state is not None:
                        err = turn_state.record_step_outcome(
                            step_id=sid,
                            kind=str(kind_by_id.get(sid) or step.kind),
                            status=st,
                            message=msg,
                            source="PlanExecutor",
                        )
                        if err is not None:
                            errors.append(err)
                    else:
                        severity = "info" if st == "skipped" else "error"
                        code = (
                            f"STEP_SKIPPED_{str(kind_by_id.get(sid) or step.kind).upper()}"
                            if st == "skipped"
                            else f"STEP_ERROR_{str(kind_by_id.get(sid) or step.kind).upper()}"
                        )
                        errors.append(
                            TurnError(
                                code=code,
                                message=msg or st,
                                source="PlanExecutor",
                                step_id=sid,
                                severity=severity,  # type: ignore[arg-type]
                                details={"kind": kind_by_id.get(sid) or step.kind, "status": st},
                            )
                        )

            # If a retrieve wave produced evidence, announce aggregate.
            if any(s.kind == "retrieve" for s in wave_steps):
                await self._emit_stage(
                    on_stage,
                    "RetrievalAgent",
                    "success" if evidence_bag else "empty",
                    f"累计证据 {len(evidence_bag)} 条",
                )

        retrieval_result = RetrievalResult(
            evidence=evidence_bag,
            trace=traces,
            warnings=warnings,
            latency_ms=total_latency,
            rerank_applied=rerank_applied,
        )
        if turn_state is not None:
            turn_state.plan = steps
            turn_state.retrieval_result = retrieval_result
            turn_state.skill_results = list(skill_results)
            turn_state.suggested_skills = list(suggested_skills)
            turn_state.waves = waves
            turn_state.parallel_used = parallel_used
            turn_state.used_l2 = True
            for w in warnings:
                if w not in turn_state.warnings:
                    turn_state.warnings.append(w)

        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "PlanExecutor",
                        "status": "success",
                        "summary": (
                            f"L2 完成 waves={len(waves)} evidence={len(evidence_bag)} "
                            f"parallel={'yes' if parallel_used else 'no'} "
                            f"errors={len(errors)}"
                        ),
                        "output": {
                            "waves": waves,
                            "parallel_used": parallel_used,
                            "evidence_count": len(evidence_bag),
                            "skill_results": skill_results,
                            "step_status": status_by_id,
                            "errors": [e.model_dump(mode="json") for e in errors],
                        },
                    }
                ]
            },
        )
        return PlanExecutorResult(
            plan_steps=steps,
            evidence=evidence_bag,
            retrieval_result=retrieval_result,
            skill_results=skill_results,
            suggested_skills=suggested_skills,
            waves=waves,
            parallel_used=parallel_used,
            warnings=warnings,
            errors=errors if turn_state is None else list(turn_state.errors),
        )
