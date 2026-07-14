"""CRUD-RAG style dataset import for retrieval evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    Department,
    EvalCase,
    KnowledgeBase,
    KnowledgeBasePermission,
    KnowledgeDocument,
    RagIndexJob,
    RetrievalEvalItem,
    User,
    new_id,
    utc_now,
)
from backend.app.rag.protocols import DocumentIndexer
from backend.app.services.permission_service import (
    get_knowledge_base,
    require_knowledge_base_permission,
)

CrudTaskType = Literal["questanswer_1doc", "questanswer_2docs", "questanswer_3docs"]

# Dedicated sandbox for evaluation corpora — never mix into business KBs by default.
EVAL_TEST_KB_CODE = "eval_test"
EVAL_TEST_KB_NAME = "测试库"
EVAL_TEST_KB_DESCRIPTION = "评估/回归专用沙箱知识库，仅放 CRUD 评测语料，勿写入正式制度"


class CrudImportRequest(BaseModel):
    knowledge_base_id: str | None = Field(
        default=None,
        description=(
            "Target knowledge base id. If omitted, uses/creates the dedicated "
            f"sandbox KB code={EVAL_TEST_KB_CODE} (测试库)."
        ),
    )
    source_path: str | None = Field(
        default=None,
        description="Path to split_merged.json or a directory containing it",
    )
    task_type: CrudTaskType = "questanswer_1doc"
    sample_size: int = Field(default=50, ge=1, le=2000)
    # Extra non-query documents to make retrieval non-trivial. Without distractors,
    # 1-doc CRUD QA on a tiny corpus often yields Hit@1/MRR = 1.0 and is not resume-credible.
    distractor_count: int = Field(
        default=200,
        ge=0,
        le=5000,
        description="Number of additional corpus-only documents imported as distractors",
    )
    create_eval_cases: bool = True
    index_documents: bool = True
    offset: int = Field(default=0, ge=0)
    use_eval_test_kb: bool = Field(
        default=True,
        description=(
            "When knowledge_base_id is empty, auto-ensure the dedicated eval_test KB. "
            "Set false only if you intentionally pass another knowledge_base_id."
        ),
    )


class CrudImportResult(BaseModel):
    knowledge_base_id: str
    task_type: str
    documents_created: int
    documents_reused: int
    distractor_documents_created: int = 0
    retrieval_items_created: int
    eval_cases_created: int
    indexed: int
    index_failed: int
    index_queued: int = 0
    sample_size: int
    corpus_document_count: int = 0
    source_path: str
    document_id_map: dict[str, str] = Field(default_factory=dict)
    warning: str | None = None
    # Populated for the API layer to schedule background indexing without
    # blocking the HTTP response (avoids UI spinner timeouts).
    pending_index_document_ids: list[str] = Field(default_factory=list, exclude=True)


def _default_crud_path() -> Path:
    return Path(r"D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json")


def ensure_eval_test_knowledge_base(
    session: Session,
    *,
    settings: Settings | None = None,
    actor: User | None = None,
) -> KnowledgeBase:
    """Idempotently create or revive the isolated evaluation sandbox knowledge base."""
    app_settings = settings or get_settings()
    existing = session.exec(
        select(KnowledgeBase).where(KnowledgeBase.code == EVAL_TEST_KB_CODE)
    ).first()

    admin_department = session.exec(
        select(Department).where(Department.code == "admin")
    ).first()
    if admin_department is None:
        admin_department = session.exec(select(Department)).first()
    if admin_department is None:
        raise ApplicationError(
            "DEPARTMENT_NOT_FOUND",
            "Cannot create eval_test KB: no department available",
            500,
        )

    workspace = app_settings.RAG_WORKSPACE_DIR / EVAL_TEST_KB_CODE
    Path(workspace).mkdir(parents=True, exist_ok=True)

    if existing is not None:
        # Soft-deleted sandbox must be revived so imports don't silently target a hidden KB.
        changed = False
        if existing.status != "active":
            existing.status = "active"
            changed = True
        if existing.name != EVAL_TEST_KB_NAME:
            existing.name = EVAL_TEST_KB_NAME
            changed = True
        if existing.description != EVAL_TEST_KB_DESCRIPTION:
            existing.description = EVAL_TEST_KB_DESCRIPTION
            changed = True
        if existing.rag_workspace != str(workspace):
            existing.rag_workspace = str(workspace)
            changed = True
        if changed:
            existing.updated_at = utc_now()
            session.add(existing)
        _ensure_eval_test_permissions(
            session,
            knowledge_base_id=existing.id,
            department_id=admin_department.id,
            actor=actor,
        )
        session.commit()
        session.refresh(existing)
        return existing

    knowledge_base = KnowledgeBase(
        name=EVAL_TEST_KB_NAME,
        code=EVAL_TEST_KB_CODE,
        department_id=admin_department.id,
        description=EVAL_TEST_KB_DESCRIPTION,
        rag_workspace=str(workspace),
        default_query_mode="hybrid",
        status="active",
        created_by=actor.id if actor is not None else "system",
    )
    session.add(knowledge_base)
    session.flush()
    _ensure_eval_test_permissions(
        session,
        knowledge_base_id=knowledge_base.id,
        department_id=admin_department.id,
        actor=actor,
    )
    session.commit()
    session.refresh(knowledge_base)
    return knowledge_base


def _ensure_eval_test_permissions(
    session: Session,
    *,
    knowledge_base_id: str,
    department_id: str,
    actor: User | None,
) -> None:
    existing = session.exec(
        select(KnowledgeBasePermission).where(
            KnowledgeBasePermission.knowledge_base_id == knowledge_base_id
        )
    ).all()
    pairs = {(item.subject_type, item.subject_id) for item in existing}
    if ("department", department_id) not in pairs:
        session.add(
            KnowledgeBasePermission(
                knowledge_base_id=knowledge_base_id,
                subject_type="department",
                subject_id=department_id,
                permission="read",
            )
        )
    if actor is not None and ("user", actor.id) not in pairs:
        session.add(
            KnowledgeBasePermission(
                knowledge_base_id=knowledge_base_id,
                subject_type="user",
                subject_id=actor.id,
                permission="admin",
            )
        )


def resolve_import_knowledge_base(
    session: Session,
    user: User,
    data: CrudImportRequest,
    *,
    settings: Settings | None = None,
) -> KnowledgeBase:
    """Prefer explicit id; otherwise ensure and use dedicated 测试库."""
    kb_id = (data.knowledge_base_id or "").strip() or None
    if kb_id:
        knowledge_base = get_knowledge_base(session, kb_id)
        if knowledge_base.status != "active":
            # If user pointed at a deleted sandbox, revive it instead of failing opaquely.
            if knowledge_base.code == EVAL_TEST_KB_CODE:
                return ensure_eval_test_knowledge_base(
                    session, settings=settings, actor=user
                )
            raise ApplicationError(
                "KB_NOT_FOUND",
                "Knowledge base is not active",
                404,
                {"knowledge_base_id": kb_id, "status": knowledge_base.status},
            )
        require_knowledge_base_permission(session, user, knowledge_base, "write")
        return knowledge_base
    if not data.use_eval_test_kb:
        raise ApplicationError(
            "EVAL_KB_REQUIRED",
            "knowledge_base_id is required when use_eval_test_kb=false",
            422,
        )
    knowledge_base = ensure_eval_test_knowledge_base(
        session, settings=settings, actor=user
    )
    require_knowledge_base_permission(session, user, knowledge_base, "write")
    return knowledge_base


def _resolve_source_path(source_path: str | None) -> Path:
    if source_path:
        path = Path(source_path)
    else:
        path = _default_crud_path()
    if path.is_dir():
        candidate = path / "split_merged.json"
        if candidate.exists():
            path = candidate
        else:
            matches = list(path.glob("**/split_merged.json"))
            if not matches:
                raise ApplicationError(
                    "EVAL_DATASET_NOT_FOUND",
                    "split_merged.json not found under the given directory",
                    404,
                    {"path": str(path)},
                )
            path = matches[0]
    if not path.exists():
        raise ApplicationError(
            "EVAL_DATASET_NOT_FOUND",
            "CRUD dataset file not found",
            404,
            {"path": str(path)},
        )
    return path


def _load_task_items(path: Path, task_type: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or task_type not in data:
        raise ApplicationError(
            "EVAL_DATASET_INVALID",
            f"Dataset missing task key: {task_type}",
            422,
            {"available": list(data.keys()) if isinstance(data, dict) else []},
        )
    items = data[task_type]
    if not isinstance(items, list):
        raise ApplicationError("EVAL_DATASET_INVALID", "Task payload must be a list", 422)
    return items


def _keywords_from_answer(answer: str, limit: int = 6) -> list[str]:
    tokens = re.findall(r"[一-鿿]{2,}|[A-Za-z0-9_]{3,}", answer or "")
    unique: list[str] = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
        if len(unique) >= limit:
            break
    return unique


def _news_fields(item: dict[str, Any], task_type: str) -> list[tuple[str, str, str]]:
    """Return list of (external_id, title, text)."""
    base_id = str(item.get("ID") or item.get("id") or new_id())
    results: list[tuple[str, str, str]] = []
    if task_type == "questanswer_1doc":
        text = str(item.get("news1") or "").strip()
        title = str(item.get("event") or base_id)[:255]
        if text:
            results.append((base_id, title, text))
        return results
    for index, key in enumerate(("news1", "news2", "news3"), start=1):
        text = str(item.get(key) or "").strip()
        if not text:
            continue
        ext = f"{base_id}#{index}"
        title = f"{str(item.get('event') or base_id)[:200]} ({key})"
        results.append((ext, title, text))
    return results


def _upsert_document(
    session: Session,
    *,
    settings: Settings,
    user: User,
    knowledge_base_id: str,
    external_id: str,
    title: str,
    text: str,
) -> tuple[KnowledgeDocument, bool]:
    existing = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.external_id == external_id,
            KnowledgeDocument.index_status != "deleted",
        )
    ).first()
    if existing is not None:
        return existing, False

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    by_hash = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.content_hash == content_hash,
            KnowledgeDocument.index_status != "deleted",
        )
    ).first()
    if by_hash is not None:
        if not by_hash.external_id:
            by_hash.external_id = external_id
            by_hash.updated_at = utc_now()
            session.add(by_hash)
            session.flush()
        return by_hash, False

    document_id = new_id()
    storage_directory = settings.UPLOAD_DIR / "crud_import" / knowledge_base_id
    storage_directory.mkdir(parents=True, exist_ok=True)
    file_path = storage_directory / f"{document_id}.txt"
    file_path.write_text(text, encoding="utf-8")
    document = KnowledgeDocument(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        title=title[:255] or external_id,
        file_path=str(file_path),
        file_type="txt",
        content_text=text,
        content_hash=content_hash,
        external_id=external_id,
        created_by=user.id,
    )
    job = RagIndexJob(knowledge_document_id=document.id, job_type="insert")
    session.add(document)
    session.add(job)
    session.flush()
    return document, True


async def import_crud_dataset(
    session: Session,
    engine: Engine,
    user: User,
    data: CrudImportRequest,
    indexer: DocumentIndexer | None = None,
    settings: Settings | None = None,
) -> CrudImportResult:
    app_settings = settings or get_settings()
    knowledge_base = resolve_import_knowledge_base(
        session, user, data, settings=app_settings
    )

    source = _resolve_source_path(data.source_path)
    items = _load_task_items(source, data.task_type)
    sliced = items[data.offset : data.offset + data.sample_size]
    if not sliced:
        raise ApplicationError(
            "EVAL_DATASET_EMPTY",
            "No samples in the selected range",
            422,
            {"offset": data.offset, "sample_size": data.sample_size, "total": len(items)},
        )

    documents_created = 0
    documents_reused = 0
    distractor_documents_created = 0
    retrieval_items_created = 0
    eval_cases_created = 0
    id_map: dict[str, str] = {}
    created_document_ids: list[str] = []
    gold_external_ids: set[str] = set()

    for item in sliced:
        if not isinstance(item, dict):
            continue
        question = str(item.get("questions") or item.get("question") or "").strip()
        answer = str(item.get("answers") or item.get("answer") or "").strip()
        news_docs = _news_fields(item, data.task_type)
        if not question or not news_docs:
            continue
        # Multi-doc tasks require the expected number of news fields when available.
        expected_docs = {
            "questanswer_1doc": 1,
            "questanswer_2docs": 2,
            "questanswer_3docs": 3,
        }.get(data.task_type, 1)
        if len(news_docs) < expected_docs:
            continue

        relevant_ids: list[str] = []
        for external_id, title, text in news_docs:
            document, created = _upsert_document(
                session,
                settings=app_settings,
                user=user,
                knowledge_base_id=knowledge_base.id,
                external_id=external_id,
                title=title,
                text=text,
            )
            id_map[external_id] = document.id
            gold_external_ids.add(external_id)
            relevant_ids.append(document.id)
            if created:
                documents_created += 1
                created_document_ids.append(document.id)
            else:
                documents_reused += 1

        eval_case_id: str | None = None
        if data.create_eval_cases:
            eval_case = EvalCase(
                question=question,
                category=knowledge_base.code,
                expected_answer_keywords=_keywords_from_answer(answer),
                expected_source_documents=[
                    title or external_id for external_id, title, _ in news_docs
                ],
                expected_chunk_ids=[],
                should_answer=True,
            )
            session.add(eval_case)
            session.flush()
            eval_case_id = eval_case.id
            eval_cases_created += 1

        thoughts = str(item.get("thoughts") or "").strip()
        retrieval_item = RetrievalEvalItem(
            eval_case_id=eval_case_id,
            query=question,
            knowledge_base_ids=[knowledge_base.id],
            relevant_document_ids=relevant_ids,
            relevant_chunk_ids=[],
            relevance_judgement={
                "source": "crud_rag",
                "task_type": data.task_type,
                "external_ids": [external_id for external_id, _, _ in news_docs],
                "gold_doc_count": len(relevant_ids),
                "multi_doc": len(relevant_ids) > 1,
                "thoughts": thoughts[:2000] if thoughts else None,
                "event": str(item.get("event") or "")[:500] or None,
            },
        )
        session.add(retrieval_item)
        retrieval_items_created += 1

    # Import distractor-only documents so retrieval is not trivially perfect.
    if data.distractor_count > 0 and retrieval_items_created > 0:
        distractors_needed = data.distractor_count
        candidates: list[tuple[str, str, str]] = []
        seen_ext: set[str] = set(gold_external_ids)
        scan_order = list(range(data.offset + data.sample_size, len(items))) + list(
            range(0, min(data.offset + data.sample_size, len(items)))
        )
        for idx in scan_order:
            if len(candidates) >= distractors_needed:
                break
            raw_item = items[idx]
            if not isinstance(raw_item, dict):
                continue
            for external_id, title, text in _news_fields(raw_item, data.task_type):
                if external_id in seen_ext:
                    continue
                seen_ext.add(external_id)
                candidates.append((external_id, title, text))
                if len(candidates) >= distractors_needed:
                    break
        for external_id, title, text in candidates:
            document, created = _upsert_document(
                session,
                settings=app_settings,
                user=user,
                knowledge_base_id=knowledge_base.id,
                external_id=f"distractor:{external_id}",
                title=f"[干扰] {title}"[:255],
                text=text,
            )
            if created:
                distractor_documents_created += 1
                documents_created += 1
                created_document_ids.append(document.id)
            else:
                documents_reused += 1

    session.commit()

    corpus_document_count = len(
        session.exec(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == knowledge_base.id,
                KnowledgeDocument.index_status != "deleted",
            )
        ).all()
    )
    warning = None
    if corpus_document_count < max(50, retrieval_items_created * 3):
        warning = (
            f"语料规模偏小（当前文档 {corpus_document_count}，评测问 {retrieval_items_created}）。"
            "在 1 文档金标 + 小库场景下 Hit@1/MRR 容易虚高到 100%。"
            "建议 distractor_count>=200，或提高采样与干扰文档后再评测。"
        )

    pending_index_ids = list(created_document_ids) if data.index_documents else []
    if data.index_documents and indexer is None:
        pending_index_ids = []

    return CrudImportResult(
        knowledge_base_id=knowledge_base.id,
        task_type=data.task_type,
        documents_created=documents_created,
        documents_reused=documents_reused,
        distractor_documents_created=distractor_documents_created,
        retrieval_items_created=retrieval_items_created,
        eval_cases_created=eval_cases_created,
        indexed=0,
        index_failed=0,
        index_queued=len(pending_index_ids),
        sample_size=len(sliced),
        corpus_document_count=corpus_document_count,
        source_path=str(source),
        document_id_map=id_map,
        warning=warning,
        pending_index_document_ids=pending_index_ids,
    )
