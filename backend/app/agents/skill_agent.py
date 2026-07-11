"""Optional Skill recommendation Agent."""

from backend.app.schemas.chat import RouterResult


class SkillAgent:
    async def run(
        self,
        question: str,
        router_result: RouterResult,
        enabled: bool,
    ) -> list[dict[str, str]]:
        if not enabled:
            return []
        lowered = question.lower()
        suggestions: list[dict[str, str]] = []
        if any(term in lowered for term in ("流程", "步骤", "差旅", "process", "travel")):
            suggestions.append(
                {"name": "process_checklist", "description": "根据企业制度生成流程清单"}
            )
        if any(term in lowered for term in ("对比", "区别", "compare")):
            suggestions.append({"name": "policy_compare", "description": "对比多份制度内容"})
        if any(term in lowered for term in ("总结", "摘要", "summary")):
            suggestions.append({"name": "summary", "description": "总结制度或对话内容"})
        return suggestions
