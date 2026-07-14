"""Tool schemas and execution helpers for the chat answer loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import User
from backend.app.schemas.retrieval import RetrievalRequest, RetrievalStrategy
from backend.app.services.rag_service import RAGService
from backend.app.skills.registry import SkillRegistry
from backend.app.tools.registry import ToolRegistry

ToolEventCallback = Callable[[str, str, dict[str, Any]], Awaitable[None] | None]


CHAT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "kb.search",
            "description": "Search authorized knowledge bases for additional policy evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, rewritten for retrieval if needed.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of evidence snippets to return.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill.run",
            "description": (
                "Run a registered business skill such as process_checklist, "
                "policy_compare, or summary. Prefer when the user wants a checklist, "
                "comparison, or summary grounded in evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["process_checklist", "policy_compare", "summary"],
                    },
                    "question": {"type": "string"},
                    "text": {
                        "type": "string",
                        "description": "Optional long text for summary skill.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft.create",
            "description": "Create a draft artifact (email, checklist, application, faq).",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_type": {
                        "type": "string",
                        "enum": ["email", "checklist", "application", "faq"],
                    },
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "source_conversation_id": {"type": "string"},
                },
                "required": ["draft_type", "title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory.read",
            "description": "Read the current user's stored memory items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_type": {"type": "string", "enum": ["user"]},
                    "owner_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory.write",
            "description": "Write a non-policy preference or note into the current user's memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "memory_type": {
                        "type": "string",
                        "enum": ["user_preference", "entity", "long_term_event"],
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp.call",
            "description": "Call a configured MCP server tool (mock or real).",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server_id", "tool_name"],
            },
        },
    },
]


DEFAULT_CHAT_TOOLS: set[str] = {
    "kb.search",
    "skill.run",
    "draft.create",
    "draft.update",
    "memory.read",
    "memory.write",
    "mcp.call",
}

# Map free-form router hints to concrete tool names.
_HINT_ALIASES: dict[str, str] = {
    "kb.search": "kb.search",
    "search": "kb.search",
    "retrieve": "kb.search",
    "skill.run": "skill.run",
    "skill": "skill.run",
    "process_checklist": "skill.run",
    "policy_compare": "skill.run",
    "summary": "skill.run",
    "draft.create": "draft.create",
    "draft.update": "draft.update",
    "draft": "draft.create",
    "memory.read": "memory.read",
    "memory.write": "memory.write",
    "memory": "memory.read",
    "mcp.call": "mcp.call",
    "mcp": "mcp.call",
}


def resolve_allowed_tools(
    tool_hints: list[str] | None,
    *,
    need_skill: bool = False,
    base: set[str] | None = None,
) -> set[str]:
    """Resolve router tool_hints into an allowlist.

    Empty/unknown hints fall back to the full default set so the answer agent
    remains useful. When need_skill is true, skill.run is always included.
    """
    allowed = set(base or DEFAULT_CHAT_TOOLS)
    if not tool_hints:
        if need_skill:
            allowed.add("skill.run")
        return allowed

    mapped: set[str] = set()
    for raw in tool_hints:
        key = str(raw or "").strip().lower()
        if not key:
            continue
        if key in _HINT_ALIASES:
            mapped.add(_HINT_ALIASES[key])
            continue
        if key.startswith("skill."):
            mapped.add("skill.run")
            continue
        if key in DEFAULT_CHAT_TOOLS:
            mapped.add(key)
    if need_skill:
        mapped.add("skill.run")
    if not mapped:
        return allowed
    return {name for name in mapped if name in DEFAULT_CHAT_TOOLS}


@dataclass
class ChatToolExecutor:
    """Executes whitelisted tools for one chat turn and records audited calls."""

    session: Session
    user: User
    tool_registry: ToolRegistry
    skill_registry: SkillRegistry | None = None
    rag_service: RAGService | None = None
    knowledge_base_ids: list[str] = field(default_factory=list)
    conversation_id: str | None = None
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_LIGHTRAG_BM25
    evidence_payloads: list[dict[str, Any]] = field(default_factory=list)
    on_tool: ToolEventCallback | None = None
    allowed_tools: set[str] = field(default_factory=lambda: set(DEFAULT_CHAT_TOOLS))

    def apply_router_hints(
        self,
        tool_hints: list[str] | None,
        *,
        need_skill: bool = False,
    ) -> set[str]:
        self.allowed_tools = resolve_allowed_tools(
            tool_hints,
            need_skill=need_skill,
            base=DEFAULT_CHAT_TOOLS,
        )
        return set(self.allowed_tools)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            schema
            for schema in CHAT_TOOL_SCHEMAS
            if schema.get("function", {}).get("name") in self.allowed_tools
        ]

    async def _emit(self, name: str, status: str, payload: dict[str, Any]) -> None:
        if self.on_tool is None:
            return
        result = self.on_tool(name, status, payload)
        if result is not None:
            await result

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.allowed_tools:
            raise ApplicationError("TOOL_NOT_ALLOWED", f"Tool is not allowed: {name}", 403)
        await self._emit(name, "running", {"arguments": arguments})
        try:
            if name == "kb.search":
                output = await self._kb_search(arguments)
            elif name == "skill.run":
                output = await self._skill_run(arguments)
            else:
                response = await self.tool_registry.execute(
                    self.session,
                    name,
                    self.user,
                    arguments,
                    conversation_id=self.conversation_id,
                    agent_name="AnswerAgent",
                )
                output = {
                    "name": response.name,
                    "output": response.output,
                    "call_log_id": response.call_log_id,
                }
        except Exception as exc:
            error = (
                f"{exc.code}: {exc.message}"
                if isinstance(exc, ApplicationError)
                else f"{type(exc).__name__}: tool failed"
            )
            await self._emit(name, "failed", {"error": error})
            raise
        await self._emit(name, "success", {"output": output})
        return output

    async def _kb_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.rag_service is None:
            raise ApplicationError("TOOL_NOT_IMPLEMENTED", "kb.search is unavailable", 501)
        if not self.knowledge_base_ids:
            return {"evidence": [], "warning": "no_authorized_knowledge_bases"}
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ApplicationError("VALIDATION_ERROR", "query is required", 422)
        top_k = int(arguments.get("top_k") or 5)
        top_k = max(1, min(top_k, 10))
        # Record an audited synthetic tool log via registry only if registered;
        # otherwise run directly for honesty.
        result = await self.rag_service.retrieve(
            RetrievalRequest(
                query=query,
                knowledge_base_ids=self.knowledge_base_ids,
                strategy=self.retrieval_strategy,
                top_k=top_k,
            )
        )
        evidence = [item.model_dump(mode="json") for item in result.evidence]
        self.evidence_payloads.extend(evidence)
        if "kb.search" in self.tool_registry.handlers:
            await self.tool_registry.execute(
                self.session,
                "kb.search",
                self.user,
                {"query": query, "top_k": top_k, "result_count": len(evidence)},
                conversation_id=self.conversation_id,
                agent_name="AnswerAgent",
            )
        return {
            "evidence": evidence,
            "latency_ms": result.latency_ms,
            "warnings": result.warnings,
        }

    async def _skill_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.skill_registry is None:
            raise ApplicationError("TOOL_NOT_IMPLEMENTED", "skill.run is unavailable", 501)
        skill_name = str(arguments.get("name") or "").strip()
        if not skill_name:
            raise ApplicationError("VALIDATION_ERROR", "skill name is required", 422)
        payload: dict[str, Any] = dict(arguments)
        payload.pop("name", None)
        payload.setdefault("question", "")
        if skill_name == "process_checklist":
            payload.setdefault("evidence", self.evidence_payloads)
        elif skill_name == "policy_compare":
            policies = payload.get("policies")
            if not policies:
                payload["policies"] = [
                    {
                        "title": item.get("document_title") or item.get("title") or "证据",
                        "content": item.get("snippet") or item.get("content") or "",
                    }
                    for item in self.evidence_payloads
                ]
        elif skill_name == "summary":
            if not payload.get("text") and not payload.get("question"):
                joined = "\n".join(
                    str(item.get("snippet") or item.get("content") or "")
                    for item in self.evidence_payloads
                )
                payload["text"] = joined or payload.get("question") or ""
        response = await self.skill_registry.run(
            self.session,
            self.user,
            skill_name,
            payload,
        )
        if "skill.run" in self.tool_registry.handlers:
            await self.tool_registry.execute(
                self.session,
                "skill.run",
                self.user,
                {"name": skill_name, "status": "success"},
                conversation_id=self.conversation_id,
                agent_name="AnswerAgent",
            )
        return {"name": response.name, "output": response.output, "audit_id": response.audit_id}
