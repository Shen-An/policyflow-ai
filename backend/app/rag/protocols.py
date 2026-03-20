"""Protocols for retrieval, indexing, reranking, and language models."""

from collections.abc import Sequence
from typing import Protocol

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


class LLMService(Protocol):
    @property
    def available(self) -> bool: ...

    async def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class LightRAGBackend(Retriever, DocumentIndexer, Protocol):
    pass
