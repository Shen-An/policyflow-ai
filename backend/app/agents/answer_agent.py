"""Evidence-bound answer generation with optional non-authoritative memory context."""

from backend.app.agents.base import AnswerResult, MemoryWorkingSet
from backend.app.rag.protocols import LLMService
from backend.app.schemas.retrieval import Evidence
from backend.app.services.context_service import (
    format_history_for_prompt,
    format_memories_for_prompt,
)

NO_EVIDENCE_ANSWER = (
    "【未检索到知识库依据 · 模型参考回答 · 不可信，需你自行判断】\n"
    "当前授权知识库没有检索到可靠制度证据。以下内容仅供参考，"
    "不能替代正式制度，请与相关部门确认后再执行。"
)

NO_EVIDENCE_DISCLAIMER = (
    "【未检索到知识库依据 · 模型参考回答 · 不可信，需你自行判断】\n"
    "当前授权知识库没有检索到可靠制度证据。以下内容由模型基于通用知识生成，"
    "不能作为正式制度依据，请你自行判断，并与相关部门确认后再执行。\n\n"
)


class AnswerAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def run(
        self,
        question: str,
        evidence: list[Evidence],
        working_set: MemoryWorkingSet | None = None,
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
            "当用户要“模板/表单/清单/怎么填”时：\n"
            "1) 结合会话历史理解他要的是上一话题的可填写模板，而不是否认；\n"
            "2) 基于证据中的要求，整理成可直接套用的字段模板或清单"
            "（字段名 + 填写说明 + 必填/选填 + 对应依据引用）；\n"
            "3) 若证据未给出正式空白表单，应明确“以下为基于制度要求整理的填写模板，"
            "非正式系统表单”，并继续给出结构，而不是直接拒答；\n"
            "4) 不要因为证据里没有“模板”二字就说无法提供；\n"
            "5) 回答分点清晰，使用 [1]、[2] 标注引用。"
        )
        user_prompt = self._build_user_prompt(question, context, working_set)
        answer = await self.llm_service.complete(system_prompt, user_prompt)
        confidence = min(0.95, 0.6 + len(evidence) * 0.05)
        # Slightly lower confidence for synthetic templates assembled from rules.
        if any(token in question for token in ("模板", "表单", "样例", "怎么填", "怎么写")):
            confidence = min(confidence, 0.85)
        return AnswerResult(answer=answer, confidence_score=confidence)

    async def _run_without_evidence(
        self,
        question: str,
        working_set: MemoryWorkingSet | None,
    ) -> AnswerResult:
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
            return AnswerResult(answer=NO_EVIDENCE_ANSWER, confidence_score=0.0)

        if not answer:
            answer = "暂无法给出可参考的模型回答。"

        lowered = answer
        needs_prefix = not (
            "未检索到" in lowered
            or "没有检索到" in lowered
            or "不可信" in lowered
            or "仅供参考" in lowered
        )
        if needs_prefix:
            answer = f"{NO_EVIDENCE_DISCLAIMER}{answer}"
        return AnswerResult(answer=answer, confidence_score=0.0)

    def _build_user_prompt(
        self,
        question: str,
        evidence_block: str | None,
        working_set: MemoryWorkingSet | None,
    ) -> str:
        sections: list[str] = [f"问题：{question}"]
        if working_set is not None:
            if working_set.rolling_summary:
                sections.append(f"会话摘要（非权威）：{working_set.rolling_summary}")
            history_text = format_history_for_prompt(working_set.history)
            if history_text:
                sections.append(f"最近对话（非权威）：\n{history_text}")
            fixed_text = format_memories_for_prompt(working_set.fixed_memories)
            if fixed_text:
                sections.append(f"用户固定偏好/实体（非权威）：\n{fixed_text}")
            recalled_text = format_memories_for_prompt(working_set.recalled_memories)
            if recalled_text:
                sections.append(f"按需召回记忆（非权威）：\n{recalled_text}")
            sections.append(
                "任务提示：优先承接最近对话主题；若用户要模板/清单，"
                "请基于证据整理可填写结构，不要因证据未出现“模板”一词而拒答。"
            )
        if evidence_block is not None:
            sections.append(f"证据：\n{evidence_block}")
        return "\n".join(sections)
