"""Unified Router → Retrieval → Answer → Skill → Compliance orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlmodel import Session

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.base import MemoryWorkingSet, PipelineResult
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.grounding import estimate_answer_confidence, question_evidence_support
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.db.models import KnowledgeBase, User
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalResult
from backend.app.tools.chat_tools import ChatToolExecutor

StageCallback = Callable[[str, str, str], Awaitable[None] | None]
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class AgentPipeline:
    """Single orchestration path for chat and eval."""

    def __init__(
        self,
        router_agent: RouterAgent,
        retrieval_agent: RetrievalAgent,
        answer_agent: AnswerAgent,
        skill_agent: SkillAgent,
        compliance_agent: ComplianceAgent,
    ) -> None:
        self.router_agent = router_agent
        self.retrieval_agent = retrieval_agent
        self.answer_agent = answer_agent
        self.skill_agent = skill_agent
        self.compliance_agent = compliance_agent

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
    ) -> PipelineResult:
        await self._emit_stage(on_stage, "RouterAgent", "running", "正在分析问题领域与风险…")
        router_result = await self.router_agent.run(question, knowledge_bases)
        await self._emit_stage(
            on_stage,
            "RouterAgent",
            "success",
            (
                f"domain={router_result.domain}, task={router_result.task_type}, "
                f"risk={router_result.risk_level}, need_skill={router_result.need_skill}"
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
                            f"need_skill={router_result.need_skill}"
                        ),
                        "output": router_result.model_dump(mode="json"),
                    }
                ]
            },
        )

        # Apply router rewrite to retrieval query when present.
        effective_request = retrieval_request
        if (
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

        await self._emit_stage(on_stage, "RetrievalAgent", "running", "正在检索授权知识库…")
        retrieval_result = (
            await self.retrieval_agent.run(effective_request)
            if effective_request is not None
            else RetrievalResult(evidence=[], trace=[], latency_ms=0)
        )
        evidence_count = len(retrieval_result.evidence)
        support = question_evidence_support(question, retrieval_result.evidence)
        if evidence_count and not support["supported"]:
            # Off-topic hits (often after bad rewrite) are treated as no reliable evidence.
            await self._emit_stage(
                on_stage,
                "RetrievalAgent",
                "empty",
                (
                    f"命中 {evidence_count} 条但与原问题相关度过低"
                    f"（overlap={support['overlap_ratio']}），按无可靠证据处理"
                ),
            )
            retrieval_result = RetrievalResult(
                evidence=[],
                trace=retrieval_result.trace,
                warnings=list(retrieval_result.warnings)
                + ["OFF_TOPIC_RETRIEVAL_DROPPED"],
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

        if tool_executor is not None:
            tool_executor.evidence_payloads = [
                item.model_dump(mode="json") for item in retrieval_result.evidence
            ]
            tool_executor.knowledge_base_ids = [
                kb.id for kb in knowledge_bases
            ] or tool_executor.knowledge_base_ids
            allowed = tool_executor.apply_router_hints(
                router_result.tool_hints,
                need_skill=bool(router_result.need_skill and enable_skill),
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
                                "tool_hints": router_result.tool_hints,
                                "need_skill": router_result.need_skill,
                            },
                        }
                    ]
                },
            )

        # Skill before Answer so structured outputs can be grounded into the final reply.
        await self._emit_stage(on_stage, "SkillAgent", "running", "正在匹配/执行 Skill…")
        skill_results: list[dict[str, Any]] = []
        should_execute = (
            execute_skills
            and enable_skill
            and router_result.need_skill
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
        else:
            suggested_skills = await self.skill_agent.run(
                question, router_result, enable_skill and router_result.need_skill
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
                        },
                    }
                ]
            },
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
        )
        await self._emit_stage(on_stage, "AnswerAgent", "success", "回答已生成")
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

        await self._emit_stage(on_stage, "ComplianceAgent", "running", "正在做合规检查…")
        compliance = await self.compliance_agent.run(
            answer_result.answer,
            retrieval_result.evidence,
        )
        # Recompute confidence with verifier warnings so diagnostics match the gate.
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
        return PipelineResult(
            router_result=router_result,
            retrieval_result=retrieval_result,
            answer_result=answer_result,
            compliance=compliance,
            suggested_skills=suggested_skills,
            skill_results=skill_results,
        )
