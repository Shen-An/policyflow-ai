"""Protocols for retrieval, indexing, reranking, and language models."""

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from backend.app.db.models import KnowledgeBase, KnowledgeDocument
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class Retriever(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]: ...


class DocumentIndexer(Protocol):
    @property
    def available(self) -> bool: ...

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None: ...


class Reranker(Protocol):
    @property
    def available(self) -> bool: ...

    async def rerank(
        self,
        query: str,
        candidates: Sequence[Evidence],
        limit: int,
    ) -> list[Evidence]: ...


class ToolCallRequest(BaseModel):
    """One model-requested tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    """Chat message for multi-turn / tool loops."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class LLMCompletion(BaseModel):
    """Result of a chat completion, optionally with tool calls."""

    content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMService(Protocol):
    @property
    def available(self) -> bool: ...

    async def complete(self, system_prompt: str, user_prompt: str) -> str: ...

    async def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMCompletion:
        """Optional tool-calling completion. Default implementations may ignore tools."""
        ...


class LightRAGBackend(Retriever, DocumentIndexer, Protocol):
    pass
