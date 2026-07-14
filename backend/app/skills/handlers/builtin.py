"""Built-in Skill handlers grounded in evidence (LLM when available)."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.rag.protocols import LLMService


def _evidence_block(evidence: list[Any]) -> str:
    lines: list[str] = []
    for index, item in enumerate(evidence, start=1):
        if isinstance(item, dict):
            title = item.get("document_title") or item.get("title") or "未命名"
            snippet = item.get("snippet") or item.get("content") or ""
        else:
            title = getattr(item, "document_title", None) or "未命名"
            snippet = getattr(item, "snippet", "") or ""
        lines.append(f"[{index}] {title}: {snippet}")
    return "\n".join(lines)


def _safe_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
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


class ProcessChecklistSkill:
    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "待办流程")
        evidence = list(payload.get("evidence") or [])
        if not evidence:
            return {
                "status": "insufficient_evidence",
                "title": f"{question}流程清单",
                "steps": [],
                "message": "无可靠制度证据，拒绝编造流程清单。",
            }
        if self.llm_service is not None:
            system = (
                "你是企业制度流程整理助手。只能依据证据输出 JSON："
                '{"title": str, "conditions": [str], "materials": [str], '
                '"steps": [{"order": int, "action": str, "evidence_refs": [int]}], '
                '"deadlines": [str], "notes": [str]}。'
                "不得编造证据中没有的条款；无信息的字段用空数组。"
            )
            user = f"问题：{question}\n证据：\n{_evidence_block(evidence)}"
            try:
                raw = await self.llm_service.complete(system, user)
                parsed = _safe_json_object(raw)
                if parsed is not None:
                    parsed.setdefault("status", "ok")
                    parsed.setdefault("title", f"{question}流程清单")
                    return parsed
            except Exception:
                pass
        steps = [
            {
                "order": 1,
                "action": "确认适用制度与办理条件（见证据）",
                "evidence_refs": [1],
            },
            {
                "order": 2,
                "action": "准备制度要求的材料",
                "evidence_refs": [1],
            },
            {
                "order": 3,
                "action": "按证据要求提交审批并保留记录",
                "evidence_refs": [1],
            },
        ]
        return {
            "status": "ok_fallback",
            "title": f"{question}流程清单",
            "conditions": [],
            "materials": [],
            "steps": steps,
            "deadlines": [],
            "notes": ["LLM 不可用时的证据锚定占位清单，请人工核对。"],
            "evidence": evidence,
        }


class PolicyCompareSkill:
    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        policies = list(payload.get("policies") or [])
        if len(policies) < 2:
            return {
                "status": "insufficient_evidence",
                "comparison": [],
                "summary": "对比至少需要 2 段制度证据。",
            }
        if self.llm_service is not None:
            system = (
                "你是制度对比助手。只依据输入政策输出 JSON："
                '{"dimensions": [{"name": str, "items": [{"policy": str, "value": str}]}], '
                '"differences": [str], "summary": str}。不得编造。'
            )
            user = json.dumps(policies, ensure_ascii=False)
            try:
                raw = await self.llm_service.complete(system, user)
                parsed = _safe_json_object(raw)
                if parsed is not None:
                    parsed.setdefault("status", "ok")
                    return parsed
            except Exception:
                pass
        titles = [
            str(item.get("title") or item.get("document_title") or f"政策{index}")
            for index, item in enumerate(policies, start=1)
            if isinstance(item, dict)
        ]
        return {
            "status": "ok_fallback",
            "comparison": policies,
            "summary": f"已接收 {len(policies)} 项制度内容（{', '.join(titles)}），请人工核对差异。",
        }


class SummarySkill:
    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or payload.get("question") or "").strip()
        if not text:
            return {
                "status": "insufficient_evidence",
                "summary": "",
                "bullets": [],
                "message": "无可摘要文本。",
            }
        if self.llm_service is not None:
            system = (
                "你是制度摘要助手。输出 JSON："
                '{"summary": str, "bullets": [str], "source_note": str}。'
                "只压缩输入内容，不新增制度事实。"
            )
            try:
                raw = await self.llm_service.complete(system, text[:12000])
                parsed = _safe_json_object(raw)
                if parsed is not None:
                    parsed.setdefault("status", "ok")
                    return parsed
            except Exception:
                pass
        sentences = [
            item.strip()
            for item in text.replace("！", "。").replace("？", "。").split("。")
            if item.strip()
        ]
        bullets = sentences[:5]
        return {
            "status": "ok_fallback",
            "summary": "。".join(bullets) + ("。" if bullets else ""),
            "bullets": bullets,
            "source_note": "fallback_sentence_split",
        }
