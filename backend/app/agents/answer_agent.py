"""Evidence-bound answer generation with optional tool loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.agents.base import AnswerResult, MemoryWorkingSet
from backend.app.agents.grounding import estimate_answer_confidence
from backend.app.core.config import Settings, get_settings
from backend.app.rag.protocols import LLMCompletion, LLMMessage, LLMService
from backend.app.schemas.retrieval import Evidence
from backend.app.services.context_service import (
    format_history_for_prompt,
    format_memories_for_prompt,
)
from backend.app.tools.chat_tools import ChatToolExecutor

ToolRoundCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]

HARD_REFUSE_ANSWER = (
    "当前授权知识库没有检索到可靠制度证据，无法给出可作为制度依据的回答。"
    "请补充更具体的制度问题、选择正确知识库，或联系归口部门确认。"
)

NO_EVIDENCE_DISCLAIMER = (
    "【未检索到知识库依据 · 模型参考回答 · 不可信，需你自行判断】\n"
    "当前授权知识库没有检索到可靠制度证据。以下内容由模型基于通用知识生成，"
    "不能作为正式制度依据，请你自行判断，并与相关部门确认后再执行。\n\n"
)


class AnswerAgent:
    def __init__(
        self,
        llm_service: LLMService,
        settings: Settings | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.settings = settings or get_settings()

    async def run(
        self,
        question: str,
        evidence: list[Evidence],
        working_set: MemoryWorkingSet | None = None,
        *,
        tool_executor: ChatToolExecutor | None = None,
        on_event: ToolRoundCallback | None = None,
        skill_results: list[dict[str, Any]] | None = None,
        plan_steps: list[Any] | None = None,
    ) -> AnswerResult:
        if not evidence:
            return await self._run_without_evidence(question, working_set)

        context = chr(10).join(
            f"[{item.rank}] {item.document_title or '未命名文档'}: {item.snippet}"
            for item in evidence
        )
        system_prompt = (
            "你是企业制度助手。只能依据提供的证据回答制度事实；"
            "不得补充证据中不存在的制度事实或硬性数值。"
            "用户偏好、会话历史与记忆只能用于理解指代、任务状态和回答风格，"
            "绝不能替代或覆盖制度证据。\n"
            "若上游给出了本轮执行计划，仅作流程指引（覆盖用户多意图），"
            "不能替代证据，也不能编造未执行步骤的结果。\n"
            "若上游 Skill 已产出清单/对比/摘要结构化结果，应优先将其整理为可读回答，"
            "并继续用 [1]、[2] 标注制度证据；不要丢弃 Skill 中的步骤/维度。\n"
            "若需要补充检索、生成流程清单/对比/摘要、写草稿或读写用户记忆，"
            "可调用提供的 tools；不要编造 tool 结果。\n"
            "当用户要“模板/表单/清单/怎么填”时：\n"
            "1) 结合会话历史理解他要的是上一话题的可填写模板，而不是否认；\n"
            "2) 基于证据中的要求，整理成可直接套用的字段模板或清单"
            "（字段名 + 填写说明 + 必填/选填 + 对应依据引用）；\n"
            "3) 若证据未给出正式空白表单，应明确“以下为基于制度要求整理的填写模板，"
            "非正式系统表单”，并继续给出结构，而不是直接拒答；\n"
            "4) 不要因为证据里没有“模板”二字就说无法提供；\n"
            "5) 回答分点清晰，使用 [1]、[2] 标注引用。"
        )
        user_prompt = self._build_user_prompt(
            question,
            context,
            working_set,
            skill_results=skill_results,
            plan_steps=plan_steps,
        )
        answer, tool_trace = await self._complete_possibly_with_tools(
            system_prompt,
            user_prompt,
            tool_executor=tool_executor,
            on_event=on_event,
        )
        # Provisional confidence before verifier; pipeline may recompute with warnings.
        confidence = estimate_answer_confidence(
            answer=answer,
            evidence=evidence,
            skill_results=skill_results,
        )
        if any(token in question for token in ("模板", "表单", "样例", "怎么填", "怎么写")):
            confidence = min(confidence, 0.85)
        if tool_trace:
            confidence = min(confidence, 0.9)
        return AnswerResult(
            answer=answer,
            confidence_score=confidence,
            tool_trace=tool_trace,
        )

    async def _run_without_evidence(
        self,
        question: str,
        working_set: MemoryWorkingSet | None,
    ) -> AnswerResult:
        if self.settings.CHAT_HARD_REFUSE_WITHOUT_EVIDENCE:
            return AnswerResult(answer=HARD_REFUSE_ANSWER, confidence_score=0.0)

        system_prompt = (
            "你是企业制度助手。当前授权知识库没有检索到可靠制度依据。"
            "你可以基于一般常识与通用企业管理经验给出参考性回答，"
            "并可利用会话历史与用户偏好改善表达，但必须遵守："
            "1) 开头明确说明“知识库未检索到依据”；"
            "2) 明确说明回答来自模型推断，不可作为正式制度依据，需要用户自行判断并与相关部门确认；"
            "3) 不得伪造具体制度条款编号、公司内部文件名称或未经验证的硬性数值标准；"
            "4) 不得把记忆或历史对话当作制度证据；"
            "5) 若用户要模板/清单，可给出通用结构并标注为参考草稿；"
            "6) 回答简洁、分点，并在结尾再次提醒核实。"
        )
        user_prompt = self._build_user_prompt(question, None, working_set)
        try:
            answer = (await self.llm_service.complete(system_prompt, user_prompt)).strip()
        except Exception:
            return AnswerResult(answer=HARD_REFUSE_ANSWER, confidence_score=0.0)

        if not answer:
            answer = "暂无法给出可参考的模型回答。"

        needs_prefix = not (
            "未检索到" in answer
            or "没有检索到" in answer
            or "不可信" in answer
            or "仅供参考" in answer
        )
        if needs_prefix:
            answer = f"{NO_EVIDENCE_DISCLAIMER}{answer}"
        return AnswerResult(answer=answer, confidence_score=0.0)

    async def _complete_possibly_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tool_executor: ChatToolExecutor | None,
        on_event: ToolRoundCallback | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        tools_enabled = (
            self.settings.CHAT_TOOLS_ENABLED
            and tool_executor is not None
            and bool(tool_executor.openai_tools())
        )
        if not tools_enabled:
            answer = await self.llm_service.complete(system_prompt, user_prompt)
            return answer, []

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        tools = tool_executor.openai_tools()
        tool_trace: list[dict[str, Any]] = []
        max_rounds = max(1, int(self.settings.CHAT_TOOL_MAX_ROUNDS))

        for _ in range(max_rounds):
            completion = await self._call_tools(messages, tools)
            if not completion.has_tool_calls:
                content = (completion.content or "").strip()
                if content:
                    return content, tool_trace
                break

            messages.append(
                LLMMessage(
                    role="assistant",
                    content=completion.content,
                    tool_calls=completion.tool_calls,
                )
            )
            for call in completion.tool_calls:
                if on_event is not None:
                    event = on_event(
                        "tool_call",
                        {
                            "tool_name": call.name,
                            "arguments": call.arguments,
                            "tool_call_id": call.id,
                        },
                    )
                    if event is not None:
                        await event
                try:
                    output = await tool_executor.execute(call.name, call.arguments)
                    status = "success"
                    error_message = None
                except Exception as exc:
                    output = {"error": str(exc)}
                    status = "failed"
                    error_message = str(exc)
                tool_trace.append(
                    {
                        "tool_name": call.name,
                        "status": status,
                        "arguments": call.arguments,
                        "output": output,
                        "error_message": error_message,
                        "tool_call_id": call.id,
                    }
                )
                if on_event is not None:
                    event = on_event(
                        "tool_result",
                        {
                            "tool_name": call.name,
                            "status": status,
                            "output": output,
                            "error_message": error_message,
                            "tool_call_id": call.id,
                        },
                    )
                    if event is not None:
                        await event
                messages.append(
                    LLMMessage(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=json.dumps(output, ensure_ascii=False, default=str),
                    )
                )

        # Final pass without tools to force a textual answer.
        final = await self.llm_service.complete_with_tools(messages, tools=[])
        content = (final.content or "").strip()
        if not content:
            content = "已完成工具调用，但模型未返回最终文本回答。"
        return content, tool_trace

    async def _call_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMCompletion:
        complete_with_tools = getattr(self.llm_service, "complete_with_tools", None)
        if complete_with_tools is None:
            # Fallback for fakes that only implement complete().
            system = next((m.content or "" for m in messages if m.role == "system"), "")
            user = "\n".join(m.content or "" for m in messages if m.role != "system")
            text = await self.llm_service.complete(system, user)
            return LLMCompletion(content=text, tool_calls=[])
        return await complete_with_tools(messages, tools)

    def _build_user_prompt(
        self,
        question: str,
        evidence_block: str | None,
        working_set: MemoryWorkingSet | None,
        skill_results: list[dict[str, Any]] | None = None,
        plan_steps: list[Any] | None = None,
    ) -> str:
        sections: list[str] = [f"问题：{question}"]
        if plan_steps:
            plan_lines: list[str] = []
            for step in plan_steps:
                if hasattr(step, "model_dump"):
                    data = step.model_dump(mode="json")
                elif isinstance(step, dict):
                    data = step
                else:
                    continue
                plan_lines.append(
                    f"- [{data.get('id')}] ({data.get('kind')}) "
                    f"{data.get('title')} · {data.get('status')}"
                )
            if plan_lines:
                sections.append(
                    "本轮执行计划（流程指引，非制度证据）：\n" + "\n".join(plan_lines)
                )
        if working_set is not None:
            if working_set.rolling_summary:
                sections.append(
                    "温区·会话摘要（非权威，装配策略非物理冷存）："
                    f"{working_set.rolling_summary}"
                )
            history_text = format_history_for_prompt(working_set.history)
            if history_text:
                sections.append(f"热区·最近对话（非权威）：\n{history_text}")
            fixed_text = format_memories_for_prompt(working_set.fixed_memories)
            if fixed_text:
                sections.append(f"温区·用户固定偏好/实体（非权威）：\n{fixed_text}")
            recalled_text = format_memories_for_prompt(working_set.recalled_memories)
            if recalled_text:
                sections.append(
                    "冷区召回记忆（非权威；salience/时间只影响回忆优先级，"
                    "不可覆盖本轮制度证据）：\n"
                    f"{recalled_text}"
                )
            sections.append(
                "任务提示：优先承接最近对话主题；若用户要模板/清单，"
                "请基于证据整理可填写结构，不要因证据未出现“模板”一词而拒答。"
            )
        if skill_results:
            successful = [
                item
                for item in skill_results
                if item.get("status") == "success" and item.get("output") is not None
            ]
            if successful:
                sections.append(
                    "已执行 Skill 结构化结果（须融入最终回答，不得忽略）：\n"
                    + json.dumps(successful, ensure_ascii=False, default=str)[:6000]
                )
        if evidence_block is not None:
            sections.append(f"证据：\n{evidence_block}")
        return "\n".join(sections)
