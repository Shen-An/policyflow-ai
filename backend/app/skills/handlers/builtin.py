"""Built-in deterministic Skill handlers."""

from typing import Any


class ProcessChecklistSkill:
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "待办流程")
        evidence = payload.get("evidence") or []
        steps = ["确认适用制度与办理条件", "准备制度要求的材料", "提交审批并保留记录"]
        return {"title": f"{question}流程清单", "steps": steps, "evidence": evidence}


class PolicyCompareSkill:
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        policies = payload.get("policies") or []
        return {"comparison": policies, "summary": f"已对比 {len(policies)} 项制度内容"}


class SummarySkill:
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or payload.get("question") or "").strip()
        sentences = [item.strip() for item in text.replace("！", "。").split("。") if item.strip()]
        return {"summary": "。".join(sentences[:3]) + ("。" if sentences else "")}
