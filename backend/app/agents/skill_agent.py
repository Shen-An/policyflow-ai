"""Skill recommendation + optional execution."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from backend.app.db.models import User
from backend.app.schemas.chat import RouterResult
from backend.app.schemas.retrieval import Evidence
from backend.app.skills.registry import SkillRegistry

_TASK_TO_SKILL = {
    "process_checklist": (
        "process_checklist",
        "根据企业制度生成流程清单",
    ),
    "policy_compare": ("policy_compare", "对比多份制度内容"),
    "summary": ("summary", "总结制度或对话内容"),
}


class SkillAgent:
    """Selects skills by router intent + keywords and can execute via SkillRegistry."""

    def __init__(self, skill_registry: SkillRegistry | None = None) -> None:
        self.skill_registry = skill_registry

    def suggest(
        self,
        question: str,
        router_result: RouterResult,
        enabled: bool,
    ) -> list[dict[str, str]]:
        if not enabled:
            return []
        suggestions: list[dict[str, str]] = []
        seen: set[str] = set()

        mapped = _TASK_TO_SKILL.get(router_result.task_type)
        if mapped is not None:
            name, description = mapped
            suggestions.append({"name": name, "description": description})
            seen.add(name)

        # Honor explicit skill.run tool hints from router.
        for hint in router_result.tool_hints:
            if hint.startswith("skill.") and hint != "skill.run":
                skill_name = hint.removeprefix("skill.")
                if skill_name in _TASK_TO_SKILL and skill_name not in seen:
                    mapped_name, description = _TASK_TO_SKILL[skill_name]
                    suggestions.append({"name": mapped_name, "description": description})
                    seen.add(mapped_name)

        lowered = question.lower()
        keyword_rules = (
            (("流程", "步骤", "差旅", "清单", "process", "travel"), "process_checklist"),
            (("对比", "区别", "compare"), "policy_compare"),
            (("总结", "摘要", "summary"), "summary"),
        )
        for terms, skill_name in keyword_rules:
            if skill_name in seen:
                continue
            if any(term in lowered for term in terms):
                mapped_name, description = _TASK_TO_SKILL[skill_name]
                suggestions.append({"name": mapped_name, "description": description})
                seen.add(skill_name)

        # If router explicitly says no skill needed and task is plain QA, keep only mapped empty.
        if not router_result.need_skill and router_result.task_type == "knowledge_qa":
            return []
        return suggestions

    async def run(
        self,
        question: str,
        router_result: RouterResult,
        enabled: bool,
    ) -> list[dict[str, str]]:
        """Backward-compatible suggest-only API used by older callers."""
        return self.suggest(question, router_result, enabled)

    async def execute_suggested(
        self,
        session: Session,
        user: User,
        question: str,
        router_result: RouterResult,
        evidence: list[Evidence],
        enabled: bool,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        suggestions = self.suggest(question, router_result, enabled)
        if not suggestions or self.skill_registry is None:
            return suggestions, []

        results: list[dict[str, Any]] = []
        evidence_payload = [item.model_dump(mode="json") for item in evidence]
        for skill in suggestions:
            name = skill.get("name") or ""
            payload: dict[str, Any]
            if name == "process_checklist":
                payload = {"question": question, "evidence": evidence_payload}
            elif name == "policy_compare":
                policies = [
                    {
                        "title": item.document_title or "证据",
                        "content": item.snippet,
                    }
                    for item in evidence
                ]
                if len(policies) < 2:
                    results.append(
                        {
                            "name": name,
                            "status": "skipped",
                            "error": "policy_compare requires at least 2 evidence snippets",
                        }
                    )
                    continue
                payload = {"policies": policies}
            else:
                text = "\n".join(item.snippet for item in evidence) or question
                payload = {"text": text, "question": question}
            try:
                response = await self.skill_registry.run(session, user, name, payload)
                results.append(
                    {
                        "name": name,
                        "status": "success",
                        "output": response.output,
                        "audit_id": response.audit_id,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "name": name,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        return suggestions, results
