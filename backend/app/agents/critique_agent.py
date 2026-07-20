"""Evaluate-only critic for the reflection closed-loop.

Separate role/prompt from the answer author to reduce same-model self-consistency
bias. Never rewrites the answer — only finds problems against concrete dimensions
and may emit an explicit PASS verdict.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.core.config import Settings, get_settings
from backend.app.rag.protocols import LLMService
from backend.app.schemas.reflection import (
    CRITIQUE_DIMENSIONS,
    CritiqueIssue,
    CritiqueResult,
    CritiqueSeverity,
    CritiqueVerdict,
)
from backend.app.schemas.retrieval import Evidence

_VALID_SEVERITY = {"error", "warning"}
_VALID_VERDICT = {"PASS", "NEEDS_IMPROVEMENT"}


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


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


def _coerce_issue(raw: Any, index: int) -> CritiqueIssue | None:
    if not isinstance(raw, dict):
        return None
    problem = str(raw.get("problem") or "").strip()
    if not problem:
        return None
    dimension = str(raw.get("dimension") or "evidence_grounding").strip()
    if dimension not in CRITIQUE_DIMENSIONS:
        dimension = "evidence_grounding"
    severity_raw = str(raw.get("severity") or "warning").strip().lower()
    severity: CritiqueSeverity = "error" if severity_raw == "error" else "warning"
    issue_id = str(raw.get("id") or f"I{index}").strip() or f"I{index}"
    refs: list[int] = []
    for ref in raw.get("evidence_refs") or []:
        try:
            refs.append(int(ref))
        except (TypeError, ValueError):
            continue
    return CritiqueIssue(
        id=issue_id[:32],
        dimension=dimension,
        severity=severity,
        location=str(raw.get("location") or "")[:200],
        problem=problem[:500],
        evidence_refs=refs[:10],
        fix_hint=str(raw.get("fix_hint") or "")[:400],
    )


def normalize_critique_result(
    data: dict[str, Any] | None,
    *,
    pass_max_warnings: int = 1,
) -> CritiqueResult | None:
    """Coerce model JSON into CritiqueResult; enforce PASS exit rules."""
    if not data:
        return None
    raw_issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    issues: list[CritiqueIssue] = []
    for idx, raw in enumerate(raw_issues, start=1):
        issue = _coerce_issue(raw, idx)
        if issue is not None:
            issues.append(issue)
        if len(issues) >= 12:
            break

    verdict_raw = str(data.get("verdict") or "").strip().upper()
    if verdict_raw not in _VALID_VERDICT:
        # Infer from issues when model omitted / mistyped verdict.
        error_count = sum(1 for i in issues if i.severity == "error")
        warning_count = sum(1 for i in issues if i.severity == "warning")
        if not issues or (error_count == 0 and warning_count <= pass_max_warnings):
            verdict_raw = "PASS"
        else:
            verdict_raw = "NEEDS_IMPROVEMENT"

    # Hard PASS exit: empty issues OR no errors and warnings within budget.
    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    if not issues or (error_count == 0 and warning_count <= pass_max_warnings):
        verdict: CritiqueVerdict = "PASS"
        if not issues:
            issues = []
    else:
        verdict = "NEEDS_IMPROVEMENT"
        # If model said PASS but issues exceed budget, force improve.
        if verdict_raw == "PASS" and (error_count > 0 or warning_count > pass_max_warnings):
            verdict = "NEEDS_IMPROVEMENT"
        elif verdict_raw == "NEEDS_IMPROVEMENT":
            verdict = "NEEDS_IMPROVEMENT"

    summary = str(data.get("summary") or "").strip()[:300]
    if not summary:
        summary = "通过" if verdict == "PASS" else f"发现 {len(issues)} 个问题"
    return CritiqueResult(verdict=verdict, summary=summary, issues=issues)


class CritiqueAgent:
    """LLM critic: find problems only; explicit PASS exit."""

    def __init__(
        self,
        llm_service: LLMService | None,
        settings: Settings | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.settings = settings or get_settings()

    async def evaluate(
        self,
        *,
        question: str,
        answer: str,
        evidence: list[Evidence],
        skill_results: list[dict[str, Any]] | None = None,
        prior_rounds: list[dict[str, Any]] | None = None,
    ) -> CritiqueResult:
        pass_max = int(getattr(self.settings, "CHAT_REFLECTION_PASS_MAX_WARNINGS", 1) or 1)
        if self.llm_service is None:
            return CritiqueResult(
                verdict="PASS",
                summary="无 LLM，跳过评审",
                issues=[],
            )

        dimensions_text = "\n".join(f"- {name}" for name in CRITIQUE_DIMENSIONS)
        system_prompt = (
            "你是企业制度回答的独立评审员（critic），不是作者。\n"
            "职责：只找问题，绝不改写或重写答案正文。\n"
            "必须对照下列检查维度逐项审查（可只报告有问题的维度）：\n"
            f"{dimensions_text}\n"
            "规则：\n"
            "1) 只能依据提供的证据判断；不得编造制度事实。\n"
            "2) 若答案在各维度已足够可靠：verdict 必须为 PASS，issues 为空数组。\n"
            "3) 仅当存在需要修改的问题时：verdict=NEEDS_IMPROVEMENT，并列出具体 issues。\n"
            "4) severity=error 用于事实错误/编造/错误数值/错误引用；"
            "severity=warning 用于结构或完整度轻微问题。\n"
            "5) 不要为了挑毛病而硬找问题；PASS 是合法且期望的出口。\n"
            "6) 只输出一个 JSON 对象，不要 Markdown，不要额外解释。"
        )
        prior_block = ""
        if prior_rounds:
            prior_block = (
                "\n【上一轮已指出但可能未修好的问题】\n"
                + json.dumps(prior_rounds, ensure_ascii=False)[:1500]
                + "\n"
            )
        user_prompt = (
            f"【用户问题】\n{question}\n\n"
            f"【制度证据】\n{_format_evidence(evidence)}\n\n"
            f"【Skill 结构化结果】\n{_format_skill_results(skill_results)}\n\n"
            f"【待评审答案】\n{answer}\n"
            f"{prior_block}\n"
            "请输出 JSON，schema：\n"
            "{\n"
            '  "verdict": "PASS | NEEDS_IMPROVEMENT",\n'
            '  "summary": "一句话",\n'
            '  "issues": [\n'
            "    {\n"
            '      "id": "I1",\n'
            '      "dimension": "evidence_grounding|citation_integrity|numeric_fidelity|'
            'completeness|refuse_consistency|structure_clarity",\n'
            '      "severity": "error|warning",\n'
            '      "location": "问题所在片段提示",\n'
            '      "problem": "错在哪里",\n'
            '      "evidence_refs": [1],\n'
            '      "fix_hint": "如何在不编造事实的前提下修改"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )
        try:
            raw = await self.llm_service.complete(system_prompt, user_prompt)
        except Exception:
            return CritiqueResult(
                verdict="PASS",
                summary="评审调用失败，fail-open 保留草稿",
                issues=[],
            )
        parsed = _parse_json_object(raw or "")
        result = normalize_critique_result(parsed, pass_max_warnings=pass_max)
        if result is None:
            # Signal parse failure via a dedicated empty NEEDS? No — fail-open PASS
            # and let ReflectionLoop record REFLECTION_CRITIQUE_PARSE_ERROR.
            raise ValueError("critique_parse_error")
        return result
