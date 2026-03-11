"""Evidence-bound answer generation with mandatory refusal behavior."""

from backend.app.agents.base import AnswerResult
from backend.app.rag.protocols import LLMService
from backend.app.schemas.retrieval import Evidence

NO_EVIDENCE_ANSWER = "当前知识库未找到可靠依据，建议联系相关部门确认。"


class AnswerAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def run(self, question: str, evidence: list[Evidence]) -> AnswerResult:
        if not evidence:
            return AnswerResult(answer=NO_EVIDENCE_ANSWER, confidence_score=0.0)
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
