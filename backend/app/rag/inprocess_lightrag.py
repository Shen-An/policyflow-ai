"""In-process HKUDS LightRAG integration for PolicyFlow knowledge bases."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_logger
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, ModelProvider
from backend.app.rag.protocols import LLMService
from backend.app.schemas.retrieval import Evidence, RetrievalRequest
from backend.app.services.embedding_service import OpenAICompatibleEmbeddingService
from backend.app.services.llm_service import get_enabled_provider

logger = get_logger(__name__)


@dataclass
class _Workspace:
    signature: tuple[str, ...]
    rag: Any


class InProcessLightRAGAdapter:
    """Runs the official LightRAG engine inside the PolicyFlow backend process."""

    name = "lightrag"

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        llm_service: LLMService,
        embedding_service: OpenAICompatibleEmbeddingService,
        rag_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.llm_service = llm_service
        self.embedding_service = embedding_service
        self.rag_factory = rag_factory
        self._workspaces: dict[str, _Workspace] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._base_environment = dict(os.environ)

    def _restore_environment(self) -> None:
        for key in set(os.environ) - set(self._base_environment):
            del os.environ[key]
        os.environ.update(self._base_environment)

    @property
    def available(self) -> bool:
        return self.llm_service.available and self.embedding_service.available

    def _providers(self) -> tuple[ModelProvider, ModelProvider]:
        chat = get_enabled_provider(self.engine, "chat")
        embedding = get_enabled_provider(self.engine, "embedding")
        if chat is None or not chat.default_chat_model:
            raise ApplicationError("LLM_PROVIDER_ERROR", "No enabled Chat model is configured", 503)
        if embedding is None or not (
            embedding.default_embedding_model or embedding.default_chat_model
        ):
            raise ApplicationError(
                "EMBEDDING_PROVIDER_ERROR",
                "No enabled Embedding model is configured",
                503,
            )
        return chat, embedding

    @staticmethod
    def _signature(chat: ModelProvider, embedding: ModelProvider) -> tuple[str, ...]:
        embedding_model = embedding.default_embedding_model or embedding.default_chat_model
        return (
            chat.id,
            str(chat.updated_at),
            chat.default_chat_model,
            embedding.id,
            str(embedding.updated_at),
            str(embedding_model),
            str(embedding.config_json.get("embedding_dim", 1536)),
        )

    async def _llm(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> str:
        history = "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}"
            for item in history_messages or []
        )
        user_prompt = f"{history}\n{prompt}" if history else prompt
        return await self.llm_service.complete(
            system_prompt or "You are a knowledge graph extraction assistant.", user_prompt
        )

    async def _embed(self, texts: list[str]) -> np.ndarray:
        vectors = await self.embedding_service.embed(texts)
        return np.asarray(vectors, dtype=np.float32)

    @staticmethod
    def _lightrag_api() -> tuple[Any, Any, Any, Any]:
        environment = dict(os.environ)
        try:
            from lightrag import LightRAG, QueryParam
            from lightrag.kg.shared_storage import initialize_pipeline_status
            from lightrag.utils import EmbeddingFunc
        finally:
            for key in set(os.environ) - set(environment):
                del os.environ[key]
            os.environ.update(environment)
        return LightRAG, QueryParam, EmbeddingFunc, initialize_pipeline_status

    async def _workspace(self, knowledge_base: KnowledgeBase) -> Any:
        chat, embedding = self._providers()
        signature = self._signature(chat, embedding)
        existing = self._workspaces.get(knowledge_base.id)
        if existing is not None and existing.signature == signature:
            return existing.rag
        lock = self._locks.setdefault(knowledge_base.id, asyncio.Lock())
        async with lock:
            existing = self._workspaces.get(knowledge_base.id)
            if existing is not None and existing.signature == signature:
                return existing.rag
            if existing is not None:
                await existing.rag.finalize_storages()
            working_dir = Path(knowledge_base.rag_workspace)
            working_dir.mkdir(parents=True, exist_ok=True)
            embedding_dim = int(embedding.config_json.get("embedding_dim", 1536))
            embedding_model = embedding.default_embedding_model or embedding.default_chat_model
            llm_service = self.llm_service
            embedding_service = self.embedding_service

            async def llm_callback(
                prompt: str,
                system_prompt: str | None = None,
                history_messages: list[dict[str, Any]] | None = None,
                **_: Any,
            ) -> str:
                history = "\n".join(
                    f"{item.get('role', 'user')}: {item.get('content', '')}"
                    for item in history_messages or []
                )
                user_prompt = f"{history}\n{prompt}" if history else prompt
                return await llm_service.complete(
                    system_prompt or "You are a knowledge graph extraction assistant.",
                    user_prompt,
                )

            async def embedding_callback(texts: list[str]) -> np.ndarray:
                vectors = await embedding_service.embed(texts)
                return np.asarray(vectors, dtype=np.float32)

            lightrag_class, _, embedding_func_class, initialize_pipeline_status = (
                self._lightrag_api()
            )
            rag_factory = self.rag_factory or lightrag_class
            rag = rag_factory(
                working_dir=str(working_dir.parent),
                workspace=knowledge_base.code,
                llm_model_func=llm_callback,
                llm_model_name=chat.default_chat_model,
                embedding_func=embedding_func_class(
                    embedding_dim=embedding_dim,
                    func=embedding_callback,
                    model_name=embedding_model,
                ),
                auto_manage_storages_states=False,
                enable_llm_cache=True,
                enable_llm_cache_for_entity_extract=True,
            )
            await rag.initialize_storages()
            await initialize_pipeline_status(workspace=knowledge_base.code)
            self._workspaces[knowledge_base.id] = _Workspace(signature, rag)
            self._restore_environment()
            logger.info(
                "LightRAG workspace initialized",
                extra={
                    "knowledge_base_id": knowledge_base.id,
                    "workspace": knowledge_base.code,
                    "embedding_dimension": embedding_dim,
                },
            )
            return rag

    @staticmethod
    def _file_source(knowledge_base: KnowledgeBase, document: KnowledgeDocument) -> str:
        filename = Path(document.file_path).name
        return f"policyflow/{knowledge_base.code}/{document.id}/{filename}"

    @staticmethod
    def _document_id(file_path: str) -> str | None:
        parts = file_path.replace(chr(92), "/").split("/")
        if len(parts) >= 4 and parts[0] == "policyflow":
            return parts[2]
        return None

    async def insert_document(
        self, knowledge_base: KnowledgeBase, document: KnowledgeDocument
    ) -> None:
        if not document.content_text:
            raise ApplicationError("DOCUMENT_CONTENT_EMPTY", "Document has no indexable text", 422)
        rag = await self._workspace(knowledge_base)
        try:
            await rag.adelete_by_doc_id(document.id)
            await rag.ainsert(
                document.content_text,
                ids=document.id,
                file_paths=self._file_source(knowledge_base, document),
            )
            statuses = await rag.aget_docs_by_ids(document.id)
            status = statuses.get(document.id, {})
            if status.get("status") != "processed":
                raise RuntimeError(
                    str(status.get("error_msg") or "LightRAG did not finish indexing")
                )
        except Exception as exc:
            logger.exception(
                "LightRAG document indexing failed",
                extra={"knowledge_base_id": knowledge_base.id, "document_id": document.id},
            )
            raise ApplicationError(
                "LIGHTRAG_INDEX_ERROR", "LightRAG document indexing failed", 502
            ) from exc
        finally:
            self._restore_environment()

    async def _retrieve_workspace(
        self, knowledge_base: KnowledgeBase, request: RetrievalRequest, limit: int
    ) -> list[Evidence]:
        rag = await self._workspace(knowledge_base)
        try:
            _, query_param_class, _, _ = self._lightrag_api()
            result = await rag.aquery_data(
                request.query,
                query_param_class(
                    mode=cast(Any, request.lightrag_query_mode.value),
                    only_need_context=True,
                    top_k=max(limit, request.top_k),
                    chunk_top_k=max(limit, request.top_k),
                    enable_rerank=False,
                    include_references=True,
                ),
            )
        except Exception as exc:
            logger.exception(
                "LightRAG query failed",
                extra={
                    "knowledge_base_id": knowledge_base.id,
                    "query_mode": request.lightrag_query_mode.value,
                },
            )
            raise ApplicationError("LIGHTRAG_ERROR", "LightRAG query failed", 502) from exc
        finally:
            self._restore_environment()
        data = result.get("data") or {}
        if result.get("status") != "success":
            if not data:
                logger.info(
                    "LightRAG query returned no evidence",
                    extra={
                        "knowledge_base_id": knowledge_base.id,
                        "query_mode": request.lightrag_query_mode.value,
                    },
                )
                return []
            raise ApplicationError(
                "LIGHTRAG_ERROR", str(result.get("message") or "LightRAG query failed"), 502
            )
        chunks = data.get("chunks") or []
        evidence: list[Evidence] = []
        seen: set[tuple[str | None, str]] = set()
        with Session(self.engine) as session:
            for item in chunks:
                if not isinstance(item, dict):
                    continue
                file_path = str(item.get("file_path") or "")
                chunk_id = str(item.get("chunk_id") or "")
                document_id = self._document_id(file_path)
                if document_id is None and "-chunk-" in chunk_id:
                    document_id = chunk_id.split("-chunk-", 1)[0]
                document = session.get(KnowledgeDocument, document_id) if document_id else None
                content = str(item.get("content") or "").strip()
                identity = (document_id, chunk_id or content)
                if not content or identity in seen:
                    continue
                seen.add(identity)
                evidence.append(
                    Evidence(
                        knowledge_base_id=knowledge_base.id,
                        knowledge_base_name=knowledge_base.name,
                        document_id=document.id if document else document_id,
                        document_title=document.title if document else Path(file_path).name or None,
                        document_version=document.source_version if document else None,
                        chunk_id=chunk_id or None,
                        source_id=str(item.get("reference_id") or file_path) or None,
                        snippet=content,
                        # In-process LightRAG often lacks model scores; use rank decay and mark synthetic.
                        score=max(0.0, 1.0 - len(evidence) * 0.05),
                        retriever_type=self.name,
                        rank=len(evidence) + 1,
                        metadata={
                            "query_mode": request.lightrag_query_mode.value,
                            "reference_id": item.get("reference_id"),
                            "score_is_synthetic": True,
                            "score_method": "rank_decay",
                        },
                    )
                )
                if len(evidence) >= limit:
                    break
        return evidence

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        if not self.available:
            raise ApplicationError(
                "LIGHTRAG_UNAVAILABLE",
                "Chat and Embedding models must be configured before using LightRAG",
                503,
            )
        with Session(self.engine) as session:
            knowledge_bases = [
                knowledge_base
                for knowledge_base_id in request.knowledge_base_ids
                if (knowledge_base := session.get(KnowledgeBase, knowledge_base_id)) is not None
            ]
        results = await asyncio.gather(
            *(
                self._retrieve_workspace(knowledge_base, request, limit)
                for knowledge_base in knowledge_bases
            )
        )
        merged = [item for workspace_items in results for item in workspace_items]
        merged.sort(key=lambda item: (-(item.score or 0.0), item.knowledge_base_id, item.rank))
        return [
            item.model_copy(update={"rank": rank}) for rank, item in enumerate(merged[:limit], 1)
        ]

    async def close(self) -> None:
        workspaces = list(self._workspaces.values())
        self._workspaces.clear()
        if workspaces:
            await asyncio.gather(*(workspace.rag.finalize_storages() for workspace in workspaces))
        self._restore_environment()
