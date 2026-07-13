"""Evidence-bound answer generation with low-trust fallback when retrieval is empty."""

from backend.app.agents.base import AnswerResult
from backend.app.rag.protocols import LLMService
from backend.app.schemas.retrieval import Evidence

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

    async def run(self, question: str, evidence: list[Evidence]) -> AnswerResult:
        if not evidence:
            return await self._run_without_evidence(question)

        context = chr(10).join(
            f"[{item.rank}] {item.document_title or '未命名文档'}: {item.snippet}"
            for item in evidence
        )
        system_prompt = (
            "你是企业制度助手。只能依据提供的证据回答；不得补充证据中不存在的制度事实。"
            "回答应简洁，并使用 [1]、[2] 形式标注引用。"
        )
        user_prompt = f"问题：{question}{chr(10)}证据：{chr(10)}{context}"
        answer = await self.llm_service.complete(system_prompt, user_prompt)
        confidence = min(0.95, 0.6 + len(evidence) * 0.05)
        return AnswerResult(answer=answer, confidence_score=confidence)

    async def _run_without_evidence(self, question: str) -> AnswerResult:
        system_prompt = (
            "你是企业制度助手。当前授权知识库没有检索到可靠制度依据。"
            "你可以基于一般常识与通用企业管理经验给出参考性回答，"
            "但必须遵守："
            "1) 开头明确说明“知识库未检索到依据”；"
            "2) 明确说明回答来自模型推断，不可作为正式制度依据，需要用户自行判断并与相关部门确认；"
            "3) 不得伪造具体制度条款编号、公司内部文件名称或未经验证的硬性数值标准；"
            "4) 回答简洁、分点，并在结尾再次提醒核实。"
        )
        user_prompt = f"问题：{question}"
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
