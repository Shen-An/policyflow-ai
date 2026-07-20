"""Bounded Critique → Improve reflection loop under the centralized Supervisor.

Interview-honest design:
- two prompts with separate jobs (evaluate vs improve)
- concrete critique dimensions + explicit PASS exit
- hard max rounds (never trust the model to stop)
- only high-stakes turns (multi-step / risk / skill / low confidence)
- not peer multi-agent chat; stages write TurnState
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.agents.answer_agent import HARD_REFUSE_ANSWER
from backend.app.agents.base import AnswerResult, TurnState
from backend.app.agents.critique_agent import CritiqueAgent
from backend.app.agents.improve_agent import ImproveAgent
from backend.app.core.config import Settings, get_settings
from backend.app.schemas.chat import RouterResult
from backend.app.schemas.reflection import (
    ReflectionResult,
    ReflectionRound,
)
from backend.app.schemas.retrieval import Evidence

StageCallback = Callable[[str, str, str], Awaitable[None] | None]
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]

_REFUSE_MARKERS = (
    "没有检索到可靠制度证据",
    "未检索到知识库依据",
    "无法给出可作为制度依据",
    HARD_REFUSE_ANSWER[:20],
)

_SKILL_SUCCESS_NAMES = {
    "process_checklist",
    "policy_compare",
    "summary",
    "checklist",
    "compare",
}


def should_run_reflection(
    *,
    settings: Settings,
    allow_reflection: bool,
    answer: str,
    evidence: list[Evidence],
    confidence: float,
    router_result: RouterResult | None,
    skill_results: list[dict[str, Any]] | None,
) -> tuple[bool, str]:
    """Pure gate: (run?, skip_reason)."""
    if not allow_reflection:
        return False, "allow_reflection_false"
    if not bool(getattr(settings, "CHAT_REFLECTION_ENABLED", True)):
        return False, "disabled"
    max_rounds = int(getattr(settings, "CHAT_REFLECTION_MAX_ROUNDS", 2) or 0)
    if max_rounds < 1:
        return False, "max_rounds_lt_1"
    cleaned = (answer or "").strip()
    if not cleaned:
        return False, "empty_answer"
    if not evidence:
        return False, "hard_refuse"
    if any(marker in cleaned for marker in _REFUSE_MARKERS if marker):
        return False, "hard_refuse"
    if cleaned == HARD_REFUSE_ANSWER or cleaned.startswith(HARD_REFUSE_ANSWER[:40]):
        return False, "hard_refuse"

    router = router_result
    multi_step_on = bool(getattr(settings, "CHAT_REFLECTION_ON_MULTI_STEP", True))
    risk_on = bool(getattr(settings, "CHAT_REFLECTION_ON_RISK", True))
    skill_on = bool(getattr(settings, "CHAT_REFLECTION_ON_SKILL_SUCCESS", True))
    low_conf_on = bool(getattr(settings, "CHAT_REFLECTION_ON_LOW_CONFIDENCE", True))
    threshold = float(getattr(settings, "CHAT_REFLECTION_CONFIDENCE_THRESHOLD", 0.72) or 0.72)

    reasons: list[str] = []
    if multi_step_on and router is not None:
        difficulty = (getattr(router, "difficulty", None) or "simple").lower()
        mode = (getattr(router, "reasoning_mode", None) or "cot_direct").lower()
        complexity = (getattr(router, "complexity", None) or "simple").lower()
        if difficulty in {"multi_step", "branched"} or mode in {
            "cot_steps",
            "tot_select",
        } or complexity == "multi_step":
            reasons.append("multi_step")
    if risk_on and router is not None:
        risk = (getattr(router, "risk_level", None) or "low").lower()
        if risk in {"medium", "high"}:
            reasons.append(f"risk_{risk}")
    if skill_on and skill_results:
        for item in skill_results:
            if item.get("status") != "success":
                continue
            name = str(item.get("name") or item.get("skill_name") or "").lower()
            if name in _SKILL_SUCCESS_NAMES or any(
                token in name for token in ("checklist", "compare", "summary")
            ):
                reasons.append("skill_success")
                break
    if low_conf_on and confidence < threshold:
        reasons.append("low_confidence")

    if not reasons:
        return False, "not_high_stakes"
    return True, "+".join(reasons)


class ReflectionLoop:
    """Orchestrates CritiqueAgent ↔ ImproveAgent with a hard round cap."""

    def __init__(
        self,
        critique_agent: CritiqueAgent,
        improve_agent: ImproveAgent,
        settings: Settings | None = None,
    ) -> None:
        self.critique_agent = critique_agent
        self.improve_agent = improve_agent
        self.settings = settings or get_settings()

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
        *,
        question: str,
        answer_result: AnswerResult,
        evidence: list[Evidence],
        skill_results: list[dict[str, Any]] | None = None,
        router_result: RouterResult | None = None,
        turn_state: TurnState | None = None,
        on_stage: StageCallback | None = None,
        on_event: EventCallback | None = None,
        allow_reflection: bool = True,
    ) -> tuple[AnswerResult, ReflectionResult]:
        max_rounds = max(0, int(getattr(self.settings, "CHAT_REFLECTION_MAX_ROUNDS", 2) or 2))
        max_rounds = min(max_rounds, 3)  # hard product cap
        run, reason = should_run_reflection(
            settings=self.settings,
            allow_reflection=allow_reflection,
            answer=answer_result.answer,
            evidence=evidence,
            confidence=float(answer_result.confidence_score or 0.0),
            router_result=router_result,
            skill_results=skill_results,
        )
        if not run:
            stop: ReflectionResult
            if reason == "disabled":
                stop = ReflectionResult(
                    triggered=False,
                    skipped_reason=reason,
                    stopped_reason="disabled",
                    max_rounds=max_rounds,
                )
            elif reason == "hard_refuse":
                stop = ReflectionResult(
                    triggered=False,
                    skipped_reason=reason,
                    stopped_reason="hard_refuse",
                    max_rounds=max_rounds,
                )
            elif reason == "allow_reflection_false":
                stop = ReflectionResult(
                    triggered=False,
                    skipped_reason=reason,
                    stopped_reason="skipped",
                    max_rounds=max_rounds,
                )
            else:
                stop = ReflectionResult(
                    triggered=False,
                    skipped_reason=reason,
                    stopped_reason="not_triggered" if reason == "not_high_stakes" else "skipped",
                    max_rounds=max_rounds,
                )
            await self._emit_stage(
                on_stage,
                "ReflectionLoop",
                "skipped",
                f"跳过反思 · {reason}",
            )
            await self._emit_event(
                on_event,
                "diagnostics_partial",
                {
                    "commands": [
                        {
                            "name": "ReflectionLoop",
                            "status": "skipped",
                            "summary": f"skipped · {reason}",
                            "output": stop.model_dump(mode="json"),
                        }
                    ]
                },
            )
            if turn_state is not None:
                turn_state.reflection = stop
            return answer_result, stop

        current = answer_result
        rounds: list[ReflectionRound] = []
        final_verdict: str | None = None
        stopped_reason = "max_rounds"
        prior_issue_payloads: list[dict[str, Any]] = []

        await self._emit_stage(
            on_stage,
            "ReflectionLoop",
            "running",
            f"高风险回答进入反思 · 触发={reason} · max_rounds={max_rounds}",
        )

        for round_index in range(1, max_rounds + 1):
            await self._emit_stage(
                on_stage,
                "CritiqueAgent",
                "running",
                f"第 {round_index}/{max_rounds} 轮评审…",
            )
            try:
                critique = await self.critique_agent.evaluate(
                    question=question,
                    answer=current.answer,
                    evidence=evidence,
                    skill_results=skill_results,
                    prior_rounds=prior_issue_payloads or None,
                )
            except ValueError:
                # Parse failure: fail-open, keep draft.
                if turn_state is not None:
                    turn_state.record_error(
                        code="REFLECTION_CRITIQUE_PARSE_ERROR",
                        message="评审 JSON 解析失败，保留当前草稿",
                        source="CritiqueAgent",
                        severity="warning",
                        details={"round": round_index},
                    )
                await self._emit_stage(
                    on_stage,
                    "CritiqueAgent",
                    "warning",
                    "评审解析失败 · fail-open",
                )
                stopped_reason = "parse_error"
                reflection = ReflectionResult(
                    triggered=True,
                    skipped_reason=None,
                    rounds=rounds,
                    final_verdict=None,
                    stopped_reason="parse_error",
                    max_rounds=max_rounds,
                )
                await self._emit_event(
                    on_event,
                    "diagnostics_partial",
                    {
                        "commands": [
                            {
                                "name": "ReflectionLoop",
                                "status": "warning",
                                "summary": "parse_error · keep draft",
                                "output": reflection.model_dump(mode="json"),
                            }
                        ]
                    },
                )
                if turn_state is not None:
                    turn_state.reflection = reflection
                return current, reflection
            except Exception as exc:  # noqa: BLE001 — fail-open quality loop
                if turn_state is not None:
                    turn_state.record_error(
                        code="REFLECTION_CRITIQUE_ERROR",
                        message=str(exc)[:200],
                        source="CritiqueAgent",
                        severity="warning",
                        details={"round": round_index},
                    )
                await self._emit_stage(
                    on_stage,
                    "CritiqueAgent",
                    "warning",
                    "评审异常 · fail-open",
                )
                stopped_reason = "error"
                reflection = ReflectionResult(
                    triggered=True,
                    rounds=rounds,
                    final_verdict=None,
                    stopped_reason="error",
                    max_rounds=max_rounds,
                )
                if turn_state is not None:
                    turn_state.reflection = reflection
                return current, reflection

            final_verdict = critique.verdict
            await self._emit_stage(
                on_stage,
                "CritiqueAgent",
                "success" if critique.verdict == "PASS" else "warning",
                f"第 {round_index} 轮 · {critique.verdict} · issues={len(critique.issues)}",
            )
            await self._emit_event(
                on_event,
                "diagnostics_partial",
                {
                    "commands": [
                        {
                            "name": "CritiqueAgent",
                            "status": "success" if critique.verdict == "PASS" else "warning",
                            "summary": critique.summary or critique.verdict,
                            "output": {
                                "round_index": round_index,
                                "verdict": critique.verdict,
                                "issue_count": len(critique.issues),
                                "issues": [
                                    i.model_dump(mode="json") for i in critique.issues[:8]
                                ],
                            },
                        }
                    ]
                },
            )

            if critique.verdict == "PASS":
                rounds.append(
                    ReflectionRound(
                        round_index=round_index,
                        critique=critique,
                        improved=False,
                        answer_before=current.answer,
                        answer_after=current.answer,
                    )
                )
                stopped_reason = "pass"
                break

            # Last allowed evaluation: do not improve past max_rounds evaluations.
            # With max_rounds=2: evaluate on round 1 → improve → evaluate on round 2 → stop.
            if round_index >= max_rounds:
                rounds.append(
                    ReflectionRound(
                        round_index=round_index,
                        critique=critique,
                        improved=False,
                        answer_before=current.answer,
                        answer_after=current.answer,
                    )
                )
                stopped_reason = "max_rounds"
                if turn_state is not None:
                    turn_state.record_error(
                        code="REFLECTION_MAX_ROUNDS",
                        message=f"已达最大反思轮次 {max_rounds}，保留当前稿",
                        source="ReflectionLoop",
                        severity="info",
                        details={
                            "max_rounds": max_rounds,
                            "remaining_issues": len(critique.issues),
                        },
                    )
                break

            await self._emit_stage(
                on_stage,
                "ImproveAgent",
                "running",
                f"第 {round_index} 轮按批注改写…",
            )
            before = current.answer
            revised = await self.improve_agent.improve(
                question=question,
                answer=before,
                evidence=evidence,
                critique=critique,
                skill_results=skill_results,
            )
            improved = bool(revised.strip()) and revised.strip() != before.strip()
            if not revised.strip():
                if turn_state is not None:
                    turn_state.record_error(
                        code="REFLECTION_IMPROVE_EMPTY",
                        message="改写结果为空，保留上一稿",
                        source="ImproveAgent",
                        severity="warning",
                        details={"round": round_index},
                    )
                revised = before
                improved = False
                stopped_reason = "improve_empty"

            current = current.model_copy(update={"answer": revised})
            rounds.append(
                ReflectionRound(
                    round_index=round_index,
                    critique=critique,
                    improved=improved,
                    answer_before=before,
                    answer_after=revised,
                )
            )
            prior_issue_payloads = [
                {
                    "id": i.id,
                    "dimension": i.dimension,
                    "problem": i.problem,
                }
                for i in critique.issues
            ]
            await self._emit_stage(
                on_stage,
                "ImproveAgent",
                "success" if improved else "warning",
                f"第 {round_index} 轮改写{'完成' if improved else '无实质变化'}",
            )
            await self._emit_event(
                on_event,
                "diagnostics_partial",
                {
                    "commands": [
                        {
                            "name": "ImproveAgent",
                            "status": "success" if improved else "warning",
                            "summary": f"round={round_index} improved={improved}",
                            "output": {
                                "round_index": round_index,
                                "improved": improved,
                                "answer_preview": (revised or "")[:200],
                            },
                        }
                    ]
                },
            )

        reflection = ReflectionResult(
            triggered=True,
            skipped_reason=None,
            rounds=rounds,
            final_verdict=final_verdict,  # type: ignore[arg-type]
            stopped_reason=stopped_reason,  # type: ignore[arg-type]
            max_rounds=max_rounds,
        )
        status = "success" if stopped_reason == "pass" else "warning"
        await self._emit_stage(
            on_stage,
            "ReflectionLoop",
            status,
            f"反思结束 · {stopped_reason} · rounds={len(rounds)}",
        )
        await self._emit_event(
            on_event,
            "diagnostics_partial",
            {
                "commands": [
                    {
                        "name": "ReflectionLoop",
                        "status": status,
                        "summary": f"{stopped_reason} · rounds={len(rounds)}",
                        "output": reflection.model_dump(mode="json"),
                    }
                ]
            },
        )
        if turn_state is not None:
            turn_state.reflection = reflection
            turn_state.answer_result = current
        return current, reflection
