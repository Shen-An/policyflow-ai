"""Document-level BM25 retrieval over knowledge_documents.content_text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import KnowledgeBase, KnowledgeDocument
from backend.app.schemas.retrieval import Evidence, RetrievalRequest

_SNIPPET_TARGET = 320
_SNIPPET_HARD_CAP = 400
_WORD_PATTERN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_PATTERN = re.compile(r"[㐀-鿿]+")


def tokenize(value: str) -> list[str]:
    """Tokenize English words and Chinese character bigrams."""
    normalized = value.lower()
    tokens = _WORD_PATTERN.findall(normalized)
    for run in _CJK_PATTERN.findall(normalized):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def extract_snippet(text: str, query_tokens: list[str], target: int = _SNIPPET_TARGET) -> str:
    """Return a short window centered on the first query token match."""
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    hard_cap = min(max(target, 1), _SNIPPET_HARD_CAP)
    if len(cleaned) <= hard_cap:
        return cleaned

    lowered = cleaned.lower()
    match_index = -1
    match_length = 0
    for token in query_tokens:
        if not token:
            continue
        found = lowered.find(token.lower())
        if found >= 0 and (match_index < 0 or found < match_index):
            match_index = found
            match_length = len(token)

    if match_index < 0:
        start = 0
    else:
        start = max(0, match_index - hard_cap // 3)
        # Prefer nearby sentence boundaries when present.
        boundary = max(cleaned.rfind("。", 0, match_index), cleaned.rfind(".", 0, match_index))
        if boundary >= 0 and match_index - boundary < hard_cap // 2:
            start = boundary + 1

    end = min(len(cleaned), start + hard_cap)
    if end - start < hard_cap and start > 0:
        start = max(0, end - hard_cap)

    snippet = cleaned[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(cleaned):
        snippet = f"{snippet}..."
    return snippet[:_SNIPPET_HARD_CAP]


@dataclass
class _IndexedDocument:
    document: KnowledgeDocument
    tokens: list[str]


@dataclass
class _KbIndex:
    fingerprint: str
    documents: list[_IndexedDocument]
    bm25: BM25Okapi | None


class BM25Retriever:
    name = "bm25"

    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine
        self._indexes: dict[str, _KbIndex] = {}

    @property
    def available(self) -> bool:
        return self.engine is not None

    @staticmethod
    def _fingerprint(documents: list[KnowledgeDocument]) -> str:
        parts = [
            f"{document.id}:{document.content_hash}:{document.title}"
            for document in sorted(documents, key=lambda item: item.id)
        ]
        return "|".join(parts)

    def _load_documents(self, knowledge_base_id: str) -> list[KnowledgeDocument]:
        assert self.engine is not None
        with Session(self.engine) as session:
            rows = session.exec(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                    col(KnowledgeDocument.content_text).is_not(None),
                )
            ).all()
            documents: list[KnowledgeDocument] = []
            for row in rows:
                content = (row.content_text or "").strip()
                if not content:
                    continue
                documents.append(KnowledgeDocument.model_validate(row.model_dump()))
            return documents

    def _ensure_index(self, knowledge_base_id: str) -> _KbIndex:
        documents = self._load_documents(knowledge_base_id)
        fingerprint = self._fingerprint(documents)
        cached = self._indexes.get(knowledge_base_id)
        if cached is not None and cached.fingerprint == fingerprint:
            return cached

        indexed: list[_IndexedDocument] = []
        corpus: list[list[str]] = []
        for document in documents:
            text = f"{document.title}\n{document.content_text or ''}"
            tokens = tokenize(text)
            if not tokens:
                continue
            indexed.append(_IndexedDocument(document=document, tokens=tokens))
            corpus.append(tokens)

        bm25 = BM25Okapi(corpus) if corpus else None
        index = _KbIndex(fingerprint=fingerprint, documents=indexed, bm25=bm25)
        self._indexes[knowledge_base_id] = index
        return index

    def _knowledge_bases(self, knowledge_base_ids: list[str]) -> dict[str, KnowledgeBase]:
        assert self.engine is not None
        with Session(self.engine) as session:
            resolved: dict[str, KnowledgeBase] = {}
            for knowledge_base_id in knowledge_base_ids:
                knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
                if knowledge_base is not None:
                    resolved[knowledge_base_id] = KnowledgeBase.model_validate(
                        knowledge_base.model_dump()
                    )
            return resolved

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        if not self.available:
            raise ApplicationError(
                "RETRIEVAL_STRATEGY_UNAVAILABLE",
                "BM25 retriever is not enabled",
                503,
            )
        if limit <= 0:
            return []

        query_tokens = tokenize(request.query)
        if not query_tokens:
            return []

        knowledge_bases = self._knowledge_bases(request.knowledge_base_ids)
        scored: list[tuple[float, KnowledgeDocument, KnowledgeBase]] = []
        for knowledge_base_id in request.knowledge_base_ids:
            knowledge_base = knowledge_bases.get(knowledge_base_id)
            if knowledge_base is None:
                continue
            index = self._ensure_index(knowledge_base_id)
            if index.bm25 is None or not index.documents:
                continue
            scores = index.bm25.get_scores(query_tokens)
            for document_index, score in enumerate(scores):
                numeric = float(score)
                document_tokens = set(index.documents[document_index].tokens)
                if not document_tokens.intersection(query_tokens):
                    continue
                scored.append(
                    (
                        numeric,
                        index.documents[document_index].document,
                        knowledge_base,
                    )
                )

        scored.sort(key=lambda item: (-item[0], item[1].id))
        evidence: list[Evidence] = []
        for rank, (score, document, knowledge_base) in enumerate(scored[:limit], start=1):
            content = document.content_text or ""
            evidence.append(
                Evidence(
                    knowledge_base_id=knowledge_base.id,
                    knowledge_base_name=knowledge_base.name,
                    document_id=document.id,
                    document_title=document.title,
                    document_version=document.source_version,
                    chunk_id=None,
                    source_id=None,
                    snippet=extract_snippet(content, query_tokens),
                    score=score,
                    retriever_type=self.name,
                    rank=rank,
                    metadata={"content_hash": document.content_hash},
                )
            )
        return evidence
