"""LightRAG REST API adapter with per-knowledge-base endpoint isolation."""

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_logger
from backend.app.db.models import KnowledgeBase, KnowledgeDocument
from backend.app.schemas.retrieval import Evidence, RetrievalRequest

logger = get_logger(__name__)


class LightRAGAdapter:
    name = "lightrag"

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.settings.LIGHTRAG_BASE_URL)

    def _base_url(self, knowledge_base: KnowledgeBase) -> str:
        if self.settings.LIGHTRAG_BASE_URL is None:
            raise ApplicationError("LIGHTRAG_UNAVAILABLE", "LightRAG is not configured", 503)
        workspace = quote(knowledge_base.code, safe="")
        return self.settings.LIGHTRAG_BASE_URL.format(workspace=workspace).rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self.settings.LIGHTRAG_API_KEY:
            return {}
        return {
            self.settings.LIGHTRAG_API_KEY_HEADER: self.settings.LIGHTRAG_API_KEY,
        }

    @staticmethod
    def _file_source(knowledge_base: KnowledgeBase, document: KnowledgeDocument) -> str:
        filename = Path(document.file_path).name
        return f"policyflow/{knowledge_base.code}/{document.id}/{filename}"

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        if not self.available:
            raise ApplicationError("LIGHTRAG_UNAVAILABLE", "LightRAG is not configured", 503)
        payload = {
            "text": document.content_text or "",
            "file_source": self._file_source(knowledge_base, document),
        }
        await self._post(self._base_url(knowledge_base) + "/documents/text", payload)

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=self.settings.LIGHTRAG_TIMEOUT_SECONDS)
        owns_client = self._client is None
        try:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"response": data}
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "LightRAG upstream request failed",
                extra={
                    "upstream_url": str(exc.request.url),
                    "upstream_status_code": exc.response.status_code,
                    "upstream_response": exc.response.text[:1000],
                },
            )
            raise ApplicationError("LIGHTRAG_ERROR", "LightRAG request failed", 502) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "LightRAG request could not be completed",
                extra={"upstream_url": url, "error_type": type(exc).__name__},
            )
            raise ApplicationError("LIGHTRAG_ERROR", "LightRAG request failed", 502) from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _document_id(file_path: str) -> str | None:
        parts = file_path.replace(chr(92), "/").split("/")
        if len(parts) >= 4 and parts[0] == "policyflow":
            return parts[2]
        return None

    async def _retrieve_workspace(
        self,
        knowledge_base: KnowledgeBase,
        request: RetrievalRequest,
        limit: int,
    ) -> list[Evidence]:
        payload = {
            "query": request.query,
            "mode": request.lightrag_query_mode.value,
            "response_type": "Multiple Paragraphs",
            "include_references": True,
            "include_chunk_content": True,
            "enable_rerank": False,
            "only_need_context": True,
        }
        data = await self._post(self._base_url(knowledge_base) + "/query", payload)
        references = data.get("references", [])
        if not isinstance(references, list):
            return []

        evidence: list[Evidence] = []
        with Session(self.engine) as session:
            for source_rank, reference in enumerate(references[:limit], start=1):
                if not isinstance(reference, dict):
                    continue
                file_path = str(reference.get("file_path") or "")
                document_id = self._document_id(file_path)
                document = session.get(KnowledgeDocument, document_id) if document_id else None
                snippet = str(reference.get("content") or "").strip()
                if not snippet:
                    continue
                evidence.append(
                    Evidence(
                        knowledge_base_id=knowledge_base.id,
                        knowledge_base_name=knowledge_base.name,
                        document_id=document.id if document else document_id,
                        document_title=document.title if document else Path(file_path).name or None,
                        document_version=document.source_version if document else None,
                        chunk_id=str(reference.get("chunk_id"))
                        if reference.get("chunk_id")
                        else None,
                        source_id=str(reference.get("reference_id"))
                        if reference.get("reference_id")
                        else None,
                        snippet=snippet,
                        score=float(reference["score"])
                        if reference.get("score") is not None
                        else None,
                        retriever_type=self.name,
                        rank=source_rank,
                        metadata={"file_path": file_path},
                    )
                )
        return evidence

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        if not self.available:
            raise ApplicationError("LIGHTRAG_UNAVAILABLE", "LightRAG is not configured", 503)
        with Session(self.engine) as session:
            knowledge_bases = [
                knowledge_base
                for knowledge_base_id in request.knowledge_base_ids
                if (knowledge_base := session.get(KnowledgeBase, knowledge_base_id)) is not None
            ]
        workspace_results = [
            await self._retrieve_workspace(knowledge_base, request, limit)
            for knowledge_base in knowledge_bases
        ]
        merged: list[Evidence] = []
        for position in range(max((len(items) for items in workspace_results), default=0)):
            for items in workspace_results:
                if position < len(items):
                    merged.append(items[position])
                    if len(merged) >= limit:
                        return merged
        return merged
