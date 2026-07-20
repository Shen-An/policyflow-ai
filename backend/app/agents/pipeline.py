"""Unified Router → Retrieval → Answer → Skill → Compliance orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from sqlmodel import Session

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.base import AnswerResult, MemoryWorkingSet, PipelineResult, TurnState
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.grounding import estimate_answer_confidence, question_evidence_support
from backend.app.agents.plan_branch import generate_plan_options, pick_recommended_option
from backend.app.agents.plan_executor import PlanExecutor, should_use_plan_executor
from backend.app.agents.plan_normalize import (
    merge_plan_tool_hints,
    normalize_plan,
    plan_needs_skill,
    primary_retrieve_query,
)
from backend.app.agents.reflection_loop import ReflectionLoop
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.core.config import Settings, get_settings
from backend.app.db.models import KnowledgeBase, User
from backend.app.schemas.chat import ComplianceResult, PlanOption, PlanStep, RouterResult
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalResult
from backend.app.tools.chat_tools import ChatToolExecutor

StageCallback = Callable[[str, str, str], Awaitable[None] | None]
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]
StepStatus = Literal["pending", "running", "success", "skipped", "error"]


class AgentPipeline:
    """Single orchestration path for chat and eval."""

    def __init__(
        self,
        router_agent: RouterAgent,
        retrieval_agent: RetrievalAgent,
        answer_agent: AnswerAgent,
        skill_agent: SkillAgent,
        compliance_agent: ComplianceAgent,
        settings: Settings | None = None,
        reflection_loop: ReflectionLoop | None = None,
    ) -> None:
        self.router_agent = router_agent
        self.retrieval_agent = retrieval_agent
        self.answer_agent = answer_agent
        self.skill_agent = skill_agent
        self.compliance_agent = compliance_agent
        self.settings = settings or get_settings()
        self.reflection_loop = reflection_loop

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

    async def _emit_plan(
        self,
        on_event: EventCallback | None,
        router_result: Any,
        plan_steps: list[PlanStep],
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "complexity": router_result.complexity,
            "plan_source": router_result.plan_source,
            "steps": [step.model_dump(mode="json") for step in plan_steps],
        }
        if extra:
            payload.update(extra)
        await self._emit_event(on_event, "plan", payload)

    async def _emit_plan_kind(
        self,
        on_event: EventCallback | None,
        plan_steps: list[PlanStep],
        kind: str,
        status: StepStatus,
        message: str = "",
    ) -> list[PlanStep]:
        updated: list[PlanStep] = []
        changed_ids: list[str] = []
        for step in plan_steps:
            if step.kind == kind and step.status in {"pending", "running"}:
                updated.append(step.model_copy(update={"status": status}))
                changed_ids.append(step.id)
            else:
                updated.append(step)
        for step in updated:
            if step.id in changed_ids:
                await self._emit_event(
                    on_event,
                    "plan_step",
                    {
                        "id": step.id,
                        "status": step.status,
                        "message": message or step.title,
                    },
                )
        return updated

    async def _run_answer_and_compliance(
        self,
        question: str,
        *,
        plan_steps: list[PlanStep],
        multi_step: bool,
        router_result: Any,
        retrieval_result: RetrievalResult,
        skill_results: list[dict[str, Any]],
        suggested_skills: list[dict[str, str]],
        working_set: MemoryWorkingSet | None,
        tool_executor: ChatToolExecutor | None,
        on_stage: StageCallback | None,
        on_event: EventCallback | None,
        used_l2: bool,
        turn_state: TurnState | None = None,
        allow_reflection: bool = True,
    ) -> PipelineResult:
        if multi_step and not used_l2:
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "answer", "running", "正在生成回答…"
            )
        elif multi_step and used_l2:
            # Mark answer steps running if still pending.
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "answer", "running", "正在生成回答…"
            )

        await self._emit_stage(on_stage, "AnswerAgent", "running", "正在生成回答…")

        async def _answer_event(event: str, payload: dict[str, Any]) -> None:
            await self._emit_event(on_event, event, payload)
            if event == "tool_call":
                await self._emit_stage(
                    on_stage,
                    "ToolCall",
                    "running",
                    f"调用工具 {payload.get('tool_name')}",
                )
            elif event == "tool_result":
                await self._emit_stage(
                    on_stage,
                    "ToolCall",
                    str(payload.get("status") or "success"),
                    f"工具 {payload.get('tool_name')} {payload.get('status')}",
                )

        answer_result = await self.answer_agent.run(
            question,
            retrieval_result.evidence,
            working_set,
            tool_executor=tool_executor,
            on_event=_answer_event if on_event is not None or on_stage is not None else None,
            skill_results=skill_results,
            plan_steps=plan_steps if multi_step else None,
        )
        await self._emit_stage(on_stage, "AnswerAgent", "success", "回答已生成")
        if multi_step:
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "answer", "success", "回答已生成"
            )
        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "AnswerAgent",
                        "status": "success",
                        "summary": (answer_result.answer or "")[:160],
                        "output": {
                            "confidence_score": answer_result.confidence_score,
                            "tool_calls": len(answer_result.tool_trace),
                            "skill_context_count": len(
                                [item for item in skill_results if item.get("status") == "success"]
                            ),
                        },
                    }
                ]
            },
        )

        # Optional Critique→Improve closed-loop (high-stakes only; hard max rounds).
        if self.reflection_loop is not None and turn_state is not None:
            turn_state.answer_result = answer_result
        if self.reflection_loop is not None:
            answer_result, reflection = await self.reflection_loop.run(
                question=question,
                answer_result=answer_result,
                evidence=retrieval_result.evidence,
                skill_results=skill_results,
                router_result=router_result,
                turn_state=turn_state,
                on_stage=on_stage,
                on_event=on_event,
                allow_reflection=allow_reflection,
            )
            if turn_state is not None:
                turn_state.reflection = reflection
                turn_state.answer_result = answer_result

        if multi_step:
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "verify", "running", "正在合规检查…"
            )
        await self._emit_stage(on_stage, "ComplianceAgent", "running", "正在做合规检查…")
        compliance = await self.compliance_agent.run(
            answer_result.answer,
            retrieval_result.evidence,
        )
        answer_result = answer_result.model_copy(
            update={
                "confidence_score": estimate_answer_confidence(
                    answer=answer_result.answer,
                    evidence=retrieval_result.evidence,
                    skill_results=skill_results,
                    compliance_warnings=compliance.warnings,
                )
            }
        )
        await self._emit_stage(
            on_stage,
            "ComplianceAgent",
            "success" if compliance.passed else "warning",
            "合规通过" if compliance.passed else "存在合规告警",
        )
        if not compliance.passed and turn_state is not None:
            turn_state.record_error(
                code="COMPLIANCE_WARNING",
                message="、".join(compliance.warnings) or "存在合规告警",
                source="ComplianceAgent",
                severity="warning",
                details={"warnings": list(compliance.warnings)},
            )
        if multi_step:
            plan_steps = await self._emit_plan_kind(
                on_event,
                plan_steps,
                "verify",
                "success" if compliance.passed else "error",
                "合规通过" if compliance.passed else "存在合规告警",
            )
            if not compliance.passed and turn_state is not None:
                for step in plan_steps:
                    if step.kind == "verify" and step.status == "error":
                        turn_state.record_step_outcome(
                            step_id=step.id,
                            kind="verify",
                            status="error",
                            message="存在合规告警",
                            source="ComplianceAgent",
                        )
            finalized: list[PlanStep] = []
            for step in plan_steps:
                if step.status == "pending":
                    next_step = step.model_copy(update={"status": "skipped"})
                    finalized.append(next_step)
                    await self._emit_event(
                        on_event,
                        "plan_step",
                        {
                            "id": next_step.id,
                            "status": "skipped",
                            "message": "未单独执行（已由流水线覆盖）",
                        },
                    )
                    if turn_state is not None:
                        turn_state.record_step_outcome(
                            step_id=next_step.id,
                            kind=next_step.kind,
                            status="skipped",
                            message="未单独执行（已由流水线覆盖）",
                            source="AgentPipeline",
                        )
                else:
                    finalized.append(step)
            plan_steps = finalized
            router_result = router_result.model_copy(update={"plan_steps": plan_steps})
            await self._emit_plan(on_event, router_result, plan_steps)

        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "ComplianceAgent",
                        "status": "success" if compliance.passed else "warning",
                        "summary": (
                            "合规通过"
                            if compliance.passed
                            else "合规告警：" + "、".join(compliance.warnings)
                        ),
                        "output": {
                            **compliance.model_dump(mode="json"),
                            "confidence_score": answer_result.confidence_score,
                        },
                    }
                ]
            },
        )
        if turn_state is not None:
            turn_state.router_result = router_result
            turn_state.plan = plan_steps
            turn_state.retrieval_result = retrieval_result
            turn_state.skill_results = list(skill_results)
            turn_state.suggested_skills = list(suggested_skills)
            turn_state.answer_result = answer_result
            turn_state.compliance = compliance
            turn_state.status = "completed"
            turn_state.used_l2 = used_l2
            turn_state.reasoning_mode = (
                getattr(router_result, "reasoning_mode", "cot_direct") or "cot_direct"
            )
            turn_state.plan_options = list(
                getattr(router_result, "plan_options", None) or []
            )
            return turn_state.to_pipeline_result()
        return PipelineResult(
            router_result=router_result,
            retrieval_result=retrieval_result,
            answer_result=answer_result,
            compliance=compliance,
            suggested_skills=suggested_skills,
            skill_results=skill_results,
            plan=plan_steps,
            status="completed",
            plan_options=list(getattr(router_result, "plan_options", None) or []),
            reasoning_mode=getattr(router_result, "reasoning_mode", "cot_direct") or "cot_direct",
            reflection=getattr(turn_state, "reflection", None) if turn_state else None,
        )

    def _empty_retrieval(self) -> RetrievalResult:
        return RetrievalResult(evidence=[], trace=[], latency_ms=0)

    def _awaiting_result(
        self,
        router_result: RouterResult,
        plan_options: list[PlanOption],
        *,
        turn_state: TurnState | None = None,
        question: str = "",
    ) -> PipelineResult:
        updated_router = router_result.model_copy(
            update={
                "plan_options": plan_options,
                "reasoning_mode": "tot_select",
                "difficulty": "branched",
                "complexity": "multi_step",
            }
        )
        if turn_state is not None:
            turn_state.question = question or turn_state.question
            turn_state.router_result = updated_router
            turn_state.plan_options = list(plan_options)
            turn_state.reasoning_mode = "tot_select"
            turn_state.status = "awaiting_plan_selection"
            turn_state.retrieval_result = self._empty_retrieval()
            turn_state.answer_result = AnswerResult(answer="", confidence_score=0.0)
            turn_state.compliance = ComplianceResult(passed=True, warnings=[])
            return turn_state.to_pipeline_result()
        return PipelineResult(
            router_result=updated_router,
            retrieval_result=self._empty_retrieval(),
            answer_result=AnswerResult(answer="", confidence_score=0.0),
            compliance=ComplianceResult(passed=True, warnings=[]),
            suggested_skills=[],
            skill_results=[],
            plan=[],
            status="awaiting_plan_selection",
            plan_options=plan_options,
            reasoning_mode="tot_select",
        )

    async def run(
        self,
        question: str,
        knowledge_bases: list[KnowledgeBase],
        retrieval_request: RetrievalRequest | None,
        enable_skill: bool = True,
        working_set: MemoryWorkingSet | None = None,
        on_stage: StageCallback | None = None,
        on_event: EventCallback | None = None,
        *,
        session: Session | None = None,
        user: User | None = None,
        tool_executor: ChatToolExecutor | None = None,
        execute_skills: bool = False,
        # ToT dual-request: skip re-route and execute selected steps.
        selected_plan_steps: list[PlanStep] | None = None,
        selected_router_result: RouterResult | None = None,
        # Eval / non-interactive: auto-pick recommended branch instead of pausing.
        hitl: bool = True,
        # Explicit reflection gate — do NOT reuse hitl (ToT auto-pick also sets hitl=False).
        allow_reflection: bool = True,
        turn_state: TurnState | None = None,
    ) -> PipelineResult:
        planning_enabled = bool(getattr(self.settings, "CHAT_PLANNING_ENABLED", True))
        max_steps = int(getattr(self.settings, "CHAT_PLAN_MAX_STEPS", 5) or 5)
        tot_enabled = bool(getattr(self.settings, "CHAT_TOT_ENABLED", True))
        tot_auto = bool(getattr(self.settings, "CHAT_TOT_AUTO_TRIGGER", True))
        tot_min = int(getattr(self.settings, "CHAT_TOT_MIN_OPTIONS", 2) or 2)
        tot_max = int(getattr(self.settings, "CHAT_TOT_MAX_OPTIONS", 3) or 3)
        l2_enabled = bool(getattr(self.settings, "CHAT_PLAN_EXECUTOR", True))
        parallel_enabled = bool(getattr(self.settings, "CHAT_PLAN_PARALLEL", True))

        state = turn_state or TurnState(question=question, status="running")
        if not state.question:
            state.question = question

        # ---------- Resume path: user-selected ToT option ----------
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
            state.router_result = router_result
            state.plan = plan_steps
            state.reasoning_mode = "tot_select"
            state.plan_options = list(getattr(router_result, "plan_options", None) or [])
            await self._emit_stage(
                on_stage,
                "RouterAgent",
                "success",
                f"ToT 用户选路 · steps={len(plan_steps)} · executor={'L2' if use_l2 else 'L1'}",
            )
            await self._emit_event(
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
            return await self._execute_plan(
                question,
                knowledge_bases,
                retrieval_request,
                router_result=router_result,
                plan_steps=plan_steps,
                multi_step=multi_step,
                use_l2=use_l2,
                parallel_enabled=parallel_enabled,
                enable_skill=enable_skill,
                execute_skills=execute_skills,
                working_set=working_set,
                tool_executor=tool_executor,
                on_stage=on_stage,
                on_event=on_event,
                session=session,
                user=user,
                turn_state=state,
                allow_reflection=allow_reflection,
            )

        await self._emit_stage(on_stage, "RouterAgent", "running", "正在分析问题领域与风险…")
        router_result = await self.router_agent.run(question, knowledge_bases)
        router_result = normalize_plan(
            question,
            router_result,
            enabled=planning_enabled,
            max_steps=max_steps,
            tot_enabled=tot_enabled,
            tot_auto_trigger=tot_auto,
        )
        plan_steps = list(router_result.plan_steps or [])
        multi_step = router_result.complexity == "multi_step" and bool(plan_steps)
        use_l2 = multi_step and should_use_plan_executor(plan_steps, enabled=l2_enabled)
        reasoning_mode = getattr(router_result, "reasoning_mode", "cot_direct") or "cot_direct"
        difficulty = getattr(router_result, "difficulty", "simple") or "simple"
        state.router_result = router_result
        state.plan = plan_steps
        state.reasoning_mode = reasoning_mode  # type: ignore[assignment]

        await self._emit_stage(
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
        await self._emit_event(
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

        # ---------- ToT pause: generate options, stop before retrieve ----------
        if difficulty == "branched" and reasoning_mode == "tot_select" and tot_enabled:
            await self._emit_stage(
                on_stage,
                "PlanBranch",
                "running",
                "生成候选执行路径供选择…",
            )
            llm = getattr(self.router_agent, "llm_service", None)
            plan_options = await generate_plan_options(
                question,
                router_result,
                llm_service=llm,
                min_options=tot_min,
                max_options=tot_max,
                max_steps=max_steps,
            )
            router_result = router_result.model_copy(
                update={
                    "plan_options": plan_options,
                    "reasoning_mode": "tot_select",
                    "difficulty": "branched",
                }
            )
            state.router_result = router_result
            state.plan_options = list(plan_options)
            state.reasoning_mode = "tot_select"
            await self._emit_event(
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
            await self._emit_stage(
                on_stage,
                "PlanBranch",
                "success",
                f"已生成 {len(plan_options)} 条候选路径，等待用户选择",
            )
            await self._emit_event(
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
                return self._awaiting_result(
                    router_result, plan_options, turn_state=state, question=question
                )

            # Non-interactive (eval): auto-pick recommended and continue.
            chosen = pick_recommended_option(plan_options)
            if chosen is None:
                state.record_error(
                    code="TOT_NO_RECOMMENDED_OPTION",
                    message="无推荐路径可自动选用",
                    source="PlanBranch",
                    severity="error",
                )
                return self._awaiting_result(
                    router_result, plan_options, turn_state=state, question=question
                )
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
            state.router_result = router_result
            state.plan = plan_steps
            await self._emit_stage(
                on_stage,
                "PlanBranch",
                "success",
                f"非交互自动选用推荐路径 {chosen.id}",
            )

        return await self._execute_plan(
            question,
            knowledge_bases,
            retrieval_request,
            router_result=router_result,
            plan_steps=plan_steps,
            multi_step=multi_step,
            use_l2=use_l2,
            parallel_enabled=parallel_enabled,
            enable_skill=enable_skill,
            execute_skills=execute_skills,
            working_set=working_set,
            tool_executor=tool_executor,
            on_stage=on_stage,
            on_event=on_event,
            session=session,
            user=user,
            turn_state=state,
            allow_reflection=allow_reflection,
        )

    async def _execute_plan(
        self,
        question: str,
        knowledge_bases: list[KnowledgeBase],
        retrieval_request: RetrievalRequest | None,
        *,
        router_result: RouterResult,
        plan_steps: list[PlanStep],
        multi_step: bool,
        use_l2: bool,
        parallel_enabled: bool,
        enable_skill: bool,
        execute_skills: bool,
        working_set: MemoryWorkingSet | None,
        tool_executor: ChatToolExecutor | None,
        on_stage: StageCallback | None,
        on_event: EventCallback | None,
        session: Session | None,
        user: User | None,
        turn_state: TurnState | None = None,
        allow_reflection: bool = True,
    ) -> PipelineResult:
        state = turn_state or TurnState(question=question, status="running")
        state.router_result = router_result
        state.plan = list(plan_steps)
        state.reasoning_mode = (
            getattr(router_result, "reasoning_mode", "cot_direct") or "cot_direct"
        )  # type: ignore[assignment]
        state.plan_options = list(getattr(router_result, "plan_options", None) or [])

        if multi_step:
            await self._emit_plan(
                on_event,
                router_result,
                plan_steps,
                extra={"executor": "L2" if use_l2 else "L1"},
            )
            await self._emit_event(
                on_event,
                "diagnostics_partial",
                {
                    "commands": [
                        {
                            "name": "Plan",
                            "status": "success",
                            "summary": (
                                f"{router_result.plan_source} · "
                                f"{len(plan_steps)} 步 · "
                                f"{'L2-executor' if use_l2 else 'L1-pipeline'}"
                            ),
                            "output": {
                                "complexity": router_result.complexity,
                                "plan_source": router_result.plan_source,
                                "executor": "L2" if use_l2 else "L1",
                                "steps": [s.model_dump(mode="json") for s in plan_steps],
                            },
                        }
                    ]
                },
            )

        # ---------- L2 path: true per-step executor with parallel waves ----------
        if use_l2:
            await self._emit_stage(
                on_stage,
                "PlanExecutor",
                "running",
                "L2 按计划执行（独立子任务可并行）…",
            )
            executor = PlanExecutor(
                self.retrieval_agent,
                self.skill_agent,
                parallel_enabled=parallel_enabled,
            )
            exec_result = await executor.run(
                question,
                plan_steps,
                router_result,
                retrieval_request,
                knowledge_bases,
                enable_skill=enable_skill,
                execute_skills=execute_skills,
                session=session,
                user=user,
                on_stage=on_stage,
                on_event=on_event,
                fallback_query=(
                    retrieval_request.query
                    if retrieval_request is not None
                    else question
                ),
                turn_state=state,
            )
            plan_steps = exec_result.plan_steps
            retrieval_result = exec_result.retrieval_result
            skill_results = exec_result.skill_results
            suggested_skills = exec_result.suggested_skills
            state.used_l2 = True
            state.waves = list(exec_result.waves)
            state.parallel_used = exec_result.parallel_used
            await self._emit_stage(
                on_stage,
                "PlanExecutor",
                "success",
                (
                    f"L2 完成 · waves={len(exec_result.waves)} · "
                    f"evidence={len(exec_result.evidence)} · "
                    f"parallel={'yes' if exec_result.parallel_used else 'no'} · "
                    f"errors={len(state.errors)}"
                ),
            )

            effective_need_skill = plan_needs_skill(
                plan_steps, bool(router_result.need_skill)
            )
            effective_tool_hints = merge_plan_tool_hints(
                plan_steps, router_result.tool_hints
            )
            router_result = router_result.model_copy(
                update={
                    "need_skill": effective_need_skill,
                    "tool_hints": effective_tool_hints,
                    "plan_steps": plan_steps,
                }
            )
            if tool_executor is not None:
                tool_executor.evidence_payloads = [
                    item.model_dump(mode="json") for item in retrieval_result.evidence
                ]
                tool_executor.knowledge_base_ids = [
                    kb.id for kb in knowledge_bases
                ] or tool_executor.knowledge_base_ids
                allowed = tool_executor.apply_router_hints(
                    effective_tool_hints,
                    need_skill=bool(effective_need_skill and enable_skill),
                )
                await self._emit_event(
                    on_event,
                    "diagnostics_partial",
                    {
                        "commands": [
                            {
                                "name": "ToolAllowlist",
                                "status": "success",
                                "summary": "tools=" + ",".join(sorted(allowed)),
                                "output": {
                                    "allowed_tools": sorted(allowed),
                                    "tool_hints": effective_tool_hints,
                                    "need_skill": effective_need_skill,
                                },
                            }
                        ]
                    },
                )
            # Skill stage summary for diagnostics (execution already done in L2).
            skill_status = "success" if (suggested_skills or skill_results) else "empty"
            if skill_results:
                ok = sum(1 for item in skill_results if item.get("status") == "success")
                skill_message = f"L2 Skill {ok}/{len(skill_results)}"
            elif suggested_skills:
                skill_message = "建议：" + "、".join(
                    item.get("name", "") for item in suggested_skills
                )
            else:
                skill_message = "无 Skill"
            await self._emit_stage(on_stage, "SkillAgent", skill_status, skill_message)
            await self._emit_event(
                on_event,
                "diagnostics_partial",
                {
                    "commands": [
                        {
                            "name": "SkillAgent",
                            "status": skill_status,
                            "summary": skill_message,
                            "output": {
                                "suggested_skills": suggested_skills,
                                "skill_results": skill_results,
                                "executor": "L2",
                            },
                        }
                    ]
                },
            )
            return await self._run_answer_and_compliance(
                question,
                plan_steps=plan_steps,
                multi_step=True,
                router_result=router_result,
                retrieval_result=retrieval_result,
                skill_results=skill_results,
                suggested_skills=suggested_skills,
                working_set=working_set,
                tool_executor=tool_executor,
                on_stage=on_stage,
                on_event=on_event,
                used_l2=True,
                turn_state=state,
                allow_reflection=allow_reflection,
            )

        # ---------- L1 / simple path ----------
        effective_request = retrieval_request
        if multi_step and effective_request is not None:
            plan_query = primary_retrieve_query(plan_steps, effective_request.query)
            if plan_query and plan_query.strip() != effective_request.query.strip():
                effective_request = effective_request.model_copy(
                    update={"query": plan_query.strip()}
                )
                await self._emit_stage(
                    on_stage,
                    "QueryRewrite",
                    "success",
                    f"计划主检索：{effective_request.query[:80]}",
                )
        elif (
            effective_request is not None
            and router_result.rewrite_query
            and router_result.rewrite_query.strip()
            and router_result.rewrite_query.strip() != effective_request.query.strip()
        ):
            effective_request = effective_request.model_copy(
                update={"query": router_result.rewrite_query.strip()}
            )
            await self._emit_stage(
                on_stage,
                "QueryRewrite",
                "success",
                f"Router 检索改写：{effective_request.query[:80]}",
            )
        elif (
            multi_step
            and effective_request is not None
            and router_result.rewrite_query
            and router_result.rewrite_query.strip()
            and router_result.rewrite_query.strip() != effective_request.query.strip()
            and not any(s.kind == "retrieve" and s.query for s in plan_steps)
        ):
            effective_request = effective_request.model_copy(
                update={"query": router_result.rewrite_query.strip()}
            )

        if multi_step:
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "retrieve", "running", "正在检索…"
            )

        await self._emit_stage(on_stage, "RetrievalAgent", "running", "正在检索授权知识库…")
        if effective_request is None or not knowledge_bases:
            state.record_error(
                code="NO_KB_FOR_RETRIEVAL",
                message="无可用知识库检索",
                source="RetrievalAgent",
                severity="error",
            )
        retrieval_result = (
            await self.retrieval_agent.run(effective_request)
            if effective_request is not None
            else RetrievalResult(evidence=[], trace=[], latency_ms=0)
        )
        evidence_count = len(retrieval_result.evidence)
        support = question_evidence_support(question, retrieval_result.evidence)
        if evidence_count and not support["supported"]:
            await self._emit_stage(
                on_stage,
                "RetrievalAgent",
                "empty",
                (
                    f"命中 {evidence_count} 条但与原问题相关度过低"
                    f"（overlap={support['overlap_ratio']}），按无可靠证据处理"
                ),
            )
            state.record_error(
                code="OFF_TOPIC_RETRIEVAL_DROPPED",
                message=(
                    f"命中 {evidence_count} 条但与原问题相关度过低"
                    f"（overlap={support['overlap_ratio']}）"
                ),
                source="RetrievalAgent",
                severity="warning",
                details={"overlap_ratio": support.get("overlap_ratio")},
            )
            retrieval_result = RetrievalResult(
                evidence=[],
                trace=retrieval_result.trace,
                warnings=list(retrieval_result.warnings) + ["OFF_TOPIC_RETRIEVAL_DROPPED"],
                latency_ms=retrieval_result.latency_ms,
                rerank_applied=retrieval_result.rerank_applied,
            )
            evidence_count = 0
        await self._emit_stage(
            on_stage,
            "RetrievalAgent",
            "success" if evidence_count else "empty",
            f"命中 {evidence_count} 条证据",
        )
        if evidence_count == 0:
            state.record_error(
                code="NO_RELIABLE_EVIDENCE",
                message="未命中可靠证据",
                source="RetrievalAgent",
                severity="error",
            )
        state.retrieval_result = retrieval_result
        if multi_step:
            plan_steps = await self._emit_plan_kind(
                on_event,
                plan_steps,
                "retrieve",
                "success" if evidence_count else "error",
                f"命中 {evidence_count} 条证据",
            )
            if evidence_count == 0:
                for step in plan_steps:
                    if step.kind == "retrieve" and step.status == "error":
                        state.record_step_outcome(
                            step_id=step.id,
                            kind="retrieve",
                            status="error",
                            message=f"命中 {evidence_count} 条证据",
                            source="RetrievalAgent",
                        )
        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "RetrievalAgent",
                        "status": "success" if evidence_count else "empty",
                        "summary": f"检索 {len(knowledge_bases)} 个知识库，命中 {evidence_count} 条证据",
                        "output": {
                            "knowledge_bases": [kb.name for kb in knowledge_bases],
                            "evidence_count": evidence_count,
                        },
                    }
                ]
            },
        )

        effective_need_skill = (
            plan_needs_skill(plan_steps, bool(router_result.need_skill))
            if multi_step
            else bool(router_result.need_skill)
        )
        effective_tool_hints = (
            merge_plan_tool_hints(plan_steps, router_result.tool_hints)
            if multi_step
            else list(router_result.tool_hints or [])
        )
        if multi_step and (
            effective_need_skill != router_result.need_skill
            or effective_tool_hints != list(router_result.tool_hints or [])
        ):
            router_result = router_result.model_copy(
                update={
                    "need_skill": effective_need_skill,
                    "tool_hints": effective_tool_hints,
                    "plan_steps": plan_steps,
                }
            )

        if tool_executor is not None:
            tool_executor.evidence_payloads = [
                item.model_dump(mode="json") for item in retrieval_result.evidence
            ]
            tool_executor.knowledge_base_ids = [
                kb.id for kb in knowledge_bases
            ] or tool_executor.knowledge_base_ids
            allowed = tool_executor.apply_router_hints(
                effective_tool_hints,
                need_skill=bool(effective_need_skill and enable_skill),
            )
            await self._emit_event(
                on_event,
                "diagnostics_partial",
                {
                    "commands": [
                        {
                            "name": "ToolAllowlist",
                            "status": "success",
                            "summary": "tools=" + ",".join(sorted(allowed)),
                            "output": {
                                "allowed_tools": sorted(allowed),
                                "tool_hints": effective_tool_hints,
                                "need_skill": effective_need_skill,
                            },
                        }
                    ]
                },
            )

        if multi_step and effective_need_skill and enable_skill:
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "skill", "running", "正在执行 Skill…"
            )
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "tool", "running", "准备工具…"
            )
        await self._emit_stage(on_stage, "SkillAgent", "running", "正在匹配/执行 Skill…")
        skill_results: list[dict[str, Any]] = []
        should_execute = (
            execute_skills
            and enable_skill
            and effective_need_skill
            and session is not None
            and user is not None
            and self.skill_agent.skill_registry is not None
        )
        if should_execute:
            suggested_skills, skill_results = await self.skill_agent.execute_suggested(
                session,
                user,
                question,
                router_result,
                retrieval_result.evidence,
                enable_skill,
            )
            for item in skill_results:
                if item.get("status") in {"error", "insufficient_evidence", "skipped"}:
                    code = str(item.get("status") or "error").upper()
                    severity = "error" if item.get("status") == "error" else "warning"
                    if item.get("status") == "skipped":
                        severity = "info"
                    state.record_error(
                        code=f"SKILL_{code}",
                        message=str(item.get("error") or item.get("status") or "skill"),
                        source="SkillAgent",
                        severity=severity,  # type: ignore[arg-type]
                        details={"skill": item.get("name") or item.get("skill_name")},
                    )
        else:
            suggested_skills = await self.skill_agent.run(
                question, router_result, enable_skill and effective_need_skill
            )
        skill_status = "success" if (suggested_skills or skill_results) else "empty"
        if skill_results:
            ok = sum(1 for item in skill_results if item.get("status") == "success")
            skill_message = f"执行 Skill {ok}/{len(skill_results)}"
        elif suggested_skills:
            skill_message = "建议：" + "、".join(
                item.get("name", "") for item in suggested_skills
            )
        else:
            skill_message = "无 Skill"
        await self._emit_stage(on_stage, "SkillAgent", skill_status, skill_message)
        if multi_step:
            skill_step_status: StepStatus = (
                "success"
                if skill_status == "success"
                else ("skipped" if not effective_need_skill or not enable_skill else "error")
            )
            if not effective_need_skill or not enable_skill:
                skill_step_status = "skipped"
            plan_steps = await self._emit_plan_kind(
                on_event, plan_steps, "skill", skill_step_status, skill_message
            )
            if skill_step_status != "success":
                for step in plan_steps:
                    if step.kind == "skill" and step.status == skill_step_status:
                        state.record_step_outcome(
                            step_id=step.id,
                            kind="skill",
                            status=skill_step_status,
                            message=skill_message,
                            source="SkillAgent",
                        )
            plan_steps = await self._emit_plan_kind(
                on_event,
                plan_steps,
                "tool",
                "success" if tool_executor is not None else "skipped",
                "工具就绪" if tool_executor is not None else "未启用工具",
            )
        state.skill_results = list(skill_results)
        state.suggested_skills = list(suggested_skills)
        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "SkillAgent",
                        "status": skill_status,
                        "summary": skill_message,
                        "output": {
                            "suggested_skills": suggested_skills,
                            "skill_results": skill_results,
                            "errors": [e.model_dump(mode="json") for e in state.errors[-5:]],
                        },
                    }
                ]
            },
        )

        return await self._run_answer_and_compliance(
            question,
            plan_steps=plan_steps,
            multi_step=multi_step,
            router_result=router_result,
            retrieval_result=retrieval_result,
            skill_results=skill_results,
            suggested_skills=suggested_skills,
            working_set=working_set,
            tool_executor=tool_executor,
            on_stage=on_stage,
            on_event=on_event,
            used_l2=False,
            turn_state=state,
            allow_reflection=allow_reflection,
        )
