"""Improve-only editor for the reflection closed-loop.

Applies critique annotations to the draft answer. Does not re-plan, re-retrieve,
or open a tool loop (phase 1). Separate role from CritiqueAgent / AnswerAgent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.core.config import Settings, get_settings
from backend.app.rag.protocols import LLMService
from backend.app.schemas.reflection import CritiqueResult
from backend.app.schemas.retrieval import Evidence


def _format_evidence(evidence: list[Evidence]) -> str:
    if not evidence:
        return "(无证据)"
    lines: list[str] = []
    for item in evidence:
        title = item.document_title or "未命名文档"
        lines.append(f"[{item.rank}] {title}: {item.snippet}")
    return "\n".join(lines)


def _format_skill_results(skill_results: list[dict[str, Any]] | None) -> str:
    if not skill_results:
        return "(无 Skill 结果)"
    chunks: list[str] = []
    for item in skill_results:
        name = item.get("name") or item.get("skill_name") or "skill"
        status = item.get("status") or "unknown"
        output = item.get("output")
        if isinstance(output, (dict, list)):
            body = json.dumps(output, ensure_ascii=False)[:1200]
        else:
            body = str(output or item.get("error") or "")[:1200]
        chunks.append(f"- {name} [{status}]: {body}")
    return "\n".join(chunks) if chunks else "(无 Skill 结果)"


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


class ImproveAgent:
    """LLM editor: rewrite only for annotated issues; evidence-bound."""

    def __init__(
        self,
        llm_service: LLMService | None,
        settings: Settings | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.settings = settings or get_settings()

    async def improve(
        self,
        *,
        question: str,
        answer: str,
        evidence: list[Evidence],
        critique: CritiqueResult,
        skill_results: list[dict[str, Any]] | None = None,
    ) -> str:
        if self.llm_service is None:
            return answer
        if not critique.issues:
            return answer

        issues_payload = [
            {
                "id": issue.id,
                "dimension": issue.dimension,
                "severity": issue.severity,
                "location": issue.location,
                "problem": issue.problem,
                "evidence_refs": issue.evidence_refs,
                "fix_hint": issue.fix_hint,
            }
            for issue in critique.issues
        ]
        system_prompt = (
            "你是企业制度回答的编辑器（editor），不是自由作者，也不是评审员。\n"
            "职责：只根据批注修改当前答案；不得新增证据中不存在的制度事实、"
            "条款编号、硬性数值或文件名。\n"
            "规则：\n"
            "1) 结合【原始任务】与【评审批注】做定向修改，不要无关重写风格。\n"
            "2) 保留并修复 [n] 引用标记，使之与证据编号一致。\n"
            "3) 不要删除与批注无关的正确内容。\n"
            "4) 不要解释你的修改过程；只输出修订后的完整答案正文。\n"
            "5) 用户偏好/记忆不得当作制度证据。"
        )
        user_prompt = (
            f"【原始任务 / 用户问题】\n{question}\n\n"
            f"【制度证据】\n{_format_evidence(evidence)}\n\n"
            f"【Skill 结构化结果】\n{_format_skill_results(skill_results)}\n\n"
            f"【当前答案】\n{answer}\n\n"
            f"【评审批注】\n{json.dumps(issues_payload, ensure_ascii=False)}\n\n"
            "请输出修订后的完整答案："
        )
        try:
            raw = await self.llm_service.complete(system_prompt, user_prompt)
        except Exception:
            return answer
        revised = _strip_fences(raw or "")
        return revised if revised else answer
