"""Evaluation datasets, asynchronous runs, history, and retrieval debug."""

from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.agents.pipeline import AgentPipeline
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    EvalCase,
    EvalResult,
    EvalRun,
    KnowledgeBase,
    KnowledgeDocument,
    RetrievalEvalItem,
    User,
)
from backend.app.evals.eval_runner import EvalRunner
from backend.app.schemas.eval import (
    EvalCaseCreate,
    EvalCaseRead,
    EvalResultRead,
    EvalRunCreate,
    EvalRunListResponse,
    EvalRunRead,
    EvalRunScopeSummary,
    EvalRunSummary,
    RetrievalDebugRequest,
    RetrievalDebugResponse,
    RetrievalEvalItemCreate,
    RetrievalEvalItemRead,
)
from backend.app.schemas.retrieval import RetrievalRequest
from backend.app.services.permission_service import (
    get_document,
    get_knowledge_base,
    require_knowledge_base_permission,
)
from backend.app.services.rag_service import RAGService


def create_eval_case(session: Session, data: EvalCaseCreate) -> EvalCaseRead:
    item = EvalCase(**data.model_dump())
    session.add(item)
    session.commit()
    session.refresh(item)
    return EvalCaseRead.model_validate(item.model_dump())


def list_eval_cases(session: Session, category: str | None = None) -> list[EvalCaseRead]:
    statement = select(EvalCase)
    if category:
        statement = statement.where(EvalCase.category == category)
    return [
        EvalCaseRead.model_validate(item.model_dump())
        for item in session.exec(statement).all()
    ]


def _validate_knowledge_bases(
    session: Session,
    user: User,
    knowledge_base_ids: list[str],
) -> list[KnowledgeBase]:
    knowledge_bases = []
    for knowledge_base_id in knowledge_base_ids:
        knowledge_base = get_knowledge_base(session, knowledge_base_id)
        require_knowledge_base_permission(session, user, knowledge_base, "read")
        knowledge_bases.append(knowledge_base)
    return knowledge_bases


def create_retrieval_item(
    session: Session,
    user: User,
    data: RetrievalEvalItemCreate,
) -> RetrievalEvalItemRead:
    if data.eval_case_id:
        eval_case = session.get(EvalCase, data.eval_case_id)
        if eval_case is None:
            raise ApplicationError("EVAL_CASE_NOT_FOUND", "Evaluation case not found", 404)
        if not eval_case.enabled:
            raise ApplicationError("EVAL_CASE_DISABLED", "Evaluation case is disabled", 409)
    _validate_knowledge_bases(session, user, data.knowledge_base_ids)
    allowed_kb_ids = set(data.knowledge_base_ids)
    for document_id in data.relevant_document_ids:
        document = get_document(session, document_id)
        if document.knowledge_base_id not in allowed_kb_ids:
            raise ApplicationError(
                "EVAL_DOCUMENT_SCOPE_INVALID",
                "Relevant document is outside the selected knowledge bases",
                422,
                {"document_id": document.id},
            )
    item = RetrievalEvalItem(**data.model_dump())
    session.add(item)
    session.commit()
    session.refresh(item)
    return RetrievalEvalItemRead.model_validate(item.model_dump())


def list_retrieval_items(
    session: Session,
    enabled: bool | None = None,
) -> list[RetrievalEvalItemRead]:
    statement = select(RetrievalEvalItem)
    if enabled is not None:
        statement = statement.where(RetrievalEvalItem.enabled == enabled)
    return [
        RetrievalEvalItemRead.model_validate(item.model_dump())
        for item in session.exec(statement).all()
    ]


def _selected_retrieval_items(
    session: Session,
    user: User,
    item_ids: list[str],
) -> list[RetrievalEvalItem]:
    items = []
    for item_id in item_ids:
        item = session.get(RetrievalEvalItem, item_id)
        if item is None:
            raise ApplicationError(
                "RETRIEVAL_EVAL_ITEM_NOT_FOUND",
                "Retrieval evaluation item not found",
                404,
            )
        if not item.enabled:
            raise ApplicationError(
                "RETRIEVAL_EVAL_ITEM_DISABLED",
                "Retrieval evaluation item is disabled",
                409,
            )
        _validate_knowledge_bases(session, user, item.knowledge_base_ids)
        items.append(item)
    return items


def _selected_cases(session: Session, user: User, case_ids: list[str]) -> list[EvalCase]:
    cases = []
    for case_id in case_ids:
        case = session.get(EvalCase, case_id)
        if case is None:
            raise ApplicationError("EVAL_CASE_NOT_FOUND", "Evaluation case not found", 404)
        if not case.enabled:
            raise ApplicationError("EVAL_CASE_DISABLED", "Evaluation case is disabled", 409)
        if case.category == "no_answer":
            knowledge_bases = session.exec(
                select(KnowledgeBase).where(KnowledgeBase.status == "active")
            ).all()
        else:
            knowledge_bases = session.exec(
                select(KnowledgeBase).where(KnowledgeBase.code == case.category)
            ).all()
        if not knowledge_bases:
            raise ApplicationError(
                "EVAL_CASE_SCOPE_INVALID",
                "No knowledge base matches the evaluation category",
                422,
                {"category": case.category},
            )
        for knowledge_base in knowledge_bases:
            require_knowledge_base_permission(session, user, knowledge_base, "read")
        cases.append(case)
    return cases


def create_eval_run(
    session: Session,
    rag_service: RAGService,
    user: User,
    data: EvalRunCreate,
    request_id: str | None = None,
) -> EvalRunRead:
    rag_service.validate_configuration(
        data.retrieval_config.strategy,
        data.retrieval_config.rerank_enabled,
    )
    _selected_retrieval_items(session, user, data.retrieval_item_ids)
    _selected_cases(session, user, data.case_ids)
    eval_run = EvalRun(
        name=data.name,
        created_by=user.id,
        config_snapshot=data.model_dump(mode="json"),
        request_id=request_id,
    )
    session.add(eval_run)
    session.commit()
    session.refresh(eval_run)
    return get_eval_run(session, eval_run.id)


async def execute_eval_run(
    engine: Engine,
    rag_service: RAGService,
    pipeline: AgentPipeline,
    run_id: str,
    data: EvalRunCreate,
) -> None:
    await EvalRunner(engine, rag_service, pipeline).run(run_id, data)


def _run_scope_summary(
    session: Session,
    config_snapshot: dict[str, Any] | None,
) -> EvalRunScopeSummary:
    """Derive KB / task_type / source from the items this run actually scored."""
    snapshot = config_snapshot or {}
    item_ids = [str(value) for value in snapshot.get("retrieval_item_ids") or [] if value]
    case_ids = [str(value) for value in snapshot.get("case_ids") or [] if value]

    knowledge_bases: dict[str, dict[str, str]] = {}
    task_types: list[str] = []
    sources: list[str] = []
    stale_gold_count = 0

    if item_ids:
        items = session.exec(
            select(RetrievalEvalItem).where(col(RetrievalEvalItem.id).in_(item_ids))
        ).all()
        kb_ids = {
            str(kb_id)
            for item in items
            for kb_id in (item.knowledge_base_ids or [])
            if kb_id
        }
        kb_rows = (
            session.exec(select(KnowledgeBase).where(col(KnowledgeBase.id).in_(list(kb_ids)))).all()
            if kb_ids
            else []
        )
        kb_by_id = {row.id: row for row in kb_rows}
        for kb_id in sorted(kb_ids):
            kb = kb_by_id.get(kb_id)
            if kb is not None:
                knowledge_bases[kb_id] = {
                    "id": kb.id,
                    "code": kb.code,
                    "name": kb.name,
                }
            else:
                knowledge_bases[kb_id] = {
                    "id": kb_id,
                    "code": "unknown",
                    "name": kb_id[:8],
                }

        gold_ids = {
            str(document_id)
            for item in items
            for document_id in (item.relevant_document_ids or [])
            if document_id
        }
        gold_rows = (
            session.exec(
                select(KnowledgeDocument).where(col(KnowledgeDocument.id).in_(list(gold_ids)))
            ).all()
            if gold_ids
            else []
        )
        gold_status = {row.id: row.index_status for row in gold_rows}

        for item in items:
            judgement = item.relevance_judgement or {}
            if isinstance(judgement, dict):
                source = judgement.get("source")
                if source and source not in sources:
                    sources.append(str(source))
                task_type = judgement.get("task_type")
                if task_type and task_type not in task_types:
                    task_types.append(str(task_type))
            item_golds = [str(value) for value in (item.relevant_document_ids or []) if value]
            if not item_golds:
                stale_gold_count += 1
                continue
            if any(
                gold_status.get(document_id) in (None, "deleted") for document_id in item_golds
            ):
                stale_gold_count += 1

    label_parts: list[str] = []
    if knowledge_bases:
        label_parts.append(
            "/".join(
                f"{item['name']}({item['code']})" for item in knowledge_bases.values()
            )
        )
    if task_types:
        label_parts.append("+".join(task_types))
    elif sources:
        label_parts.append("+".join(sources))
    if item_ids:
        label_parts.append(f"N={len(item_ids)}")
    if stale_gold_count:
        label_parts.append(f"stale_gold={stale_gold_count}")

    return EvalRunScopeSummary(
        knowledge_bases=list(knowledge_bases.values()),
        task_types=task_types,
        sources=sources,
        item_count=len(item_ids),
        case_count=len(case_ids),
        stale_gold_count=stale_gold_count,
        label=" · ".join(label_parts) if label_parts else None,
    )


def _to_eval_run_read(
    session: Session,
    eval_run: EvalRun,
    include_results: bool,
) -> EvalRunRead:
    results = (
        session.exec(select(EvalResult).where(EvalResult.eval_run_id == eval_run.id)).all()
        if include_results
        else []
    )
    return EvalRunRead(
        id=eval_run.id,
        name=eval_run.name,
        status=eval_run.status,
        total_cases=eval_run.total_cases,
        metrics=eval_run.metrics,
        config_snapshot=eval_run.config_snapshot,
        created_by=eval_run.created_by,
        created_at=eval_run.created_at,
        started_at=eval_run.started_at,
        finished_at=eval_run.finished_at,
        error_summary=eval_run.error_summary,
        request_id=eval_run.request_id,
        scope=_run_scope_summary(session, eval_run.config_snapshot),
        results=[EvalResultRead.model_validate(result.model_dump()) for result in results],
    )


def get_eval_run(session: Session, run_id: str) -> EvalRunRead:
    eval_run = session.get(EvalRun, run_id)
    if eval_run is None:
        raise ApplicationError("EVAL_RUN_NOT_FOUND", "Evaluation run not found", 404)
    session.refresh(eval_run)
    return _to_eval_run_read(session, eval_run, include_results=True)


def delete_eval_run(session: Session, run_id: str) -> None:
    """Physically delete an evaluation run and all of its per-case results."""
    eval_run = session.get(EvalRun, run_id)
    if eval_run is None:
        raise ApplicationError("EVAL_RUN_NOT_FOUND", "Evaluation run not found", 404)
    results = session.exec(
        select(EvalResult).where(EvalResult.eval_run_id == run_id)
    ).all()
    for result in results:
        session.delete(result)
    session.delete(eval_run)
    session.commit()


def export_eval_run_payload(session: Session, run_id: str) -> dict[str, Any]:
    """Structured JSON payload for interview report export."""
    run = get_eval_run(session, run_id)
    return {
        "export_version": "1.0",
        "run": {
            "id": run.id,
            "name": run.name,
            "status": run.status,
            "total_cases": run.total_cases,
            "metrics": run.metrics,
            "config_snapshot": run.config_snapshot,
            "created_by": run.created_by,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error_summary": run.error_summary,
            "request_id": run.request_id,
        },
        "results": [
            {
                "id": item.id,
                "question": item.question,
                "answer": item.answer,
                "score": item.score,
                "passed": item.passed,
                "latency_ms": item.latency_ms,
                "type_statuses": item.type_statuses,
                "retrieval_metrics": item.retrieval_metrics,
                "answer_metrics": item.answer_metrics,
                "ragas_metrics": item.ragas_metrics,
                "error_message": item.error_message,
                "retrieved_sources": item.retrieved_sources,
            }
            for item in run.results
        ],
    }


def export_eval_run_csv(session: Session, run_id: str) -> str:
    """CSV summary for spreadsheet screenshots / attachments."""
    import csv
    import io

    run = get_eval_run(session, run_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "run_id",
            "run_name",
            "run_status",
            "question",
            "passed",
            "score",
            "latency_ms",
            "mrr",
            "hit_at_5",
            "hit_all_at_5",
            "recall_at_5",
            "strategy",
            "answer_accuracy",
            "ragas_status",
            "error_message",
        ]
    )
    for item in run.results:
        retrieval = item.retrieval_metrics or {}
        answer = item.answer_metrics or {}
        ragas = item.ragas_metrics or {}
        writer.writerow(
            [
                run.id,
                run.name,
                run.status,
                item.question,
                item.passed,
                item.score,
                item.latency_ms,
                retrieval.get("mrr", ""),
                retrieval.get("hit_at_5", retrieval.get("hit_at_3", "")),
                retrieval.get("hit_all_at_5", ""),
                retrieval.get("recall_at_5", retrieval.get("recall_at_3", "")),
                retrieval.get("strategy", ""),
                answer.get("answer_accuracy", ""),
                ragas.get("status", ""),
                item.error_message or "",
            ]
        )
    return buffer.getvalue()


def list_eval_runs(
    session: Session,
    page: int,
    page_size: int,
    status: str | None = None,
    created_by: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> EvalRunListResponse:
    statement = select(EvalRun)
    if status:
        statement = statement.where(EvalRun.status == status)
    if created_by:
        statement = statement.where(EvalRun.created_by == created_by)
    if created_from:
        statement = statement.where(EvalRun.created_at >= created_from)
    if created_to:
        statement = statement.where(EvalRun.created_at <= created_to)
    runs = session.exec(statement.order_by(col(EvalRun.created_at).desc())).all()
    start = (page - 1) * page_size
    return EvalRunListResponse(
        items=[
            EvalRunSummary(
                id=item.id,
                name=item.name,
                status=item.status,
                total_cases=item.total_cases,
                created_by=item.created_by,
                created_at=item.created_at,
                started_at=item.started_at,
                finished_at=item.finished_at,
                metrics=item.metrics,
                error_summary=item.error_summary,
                request_id=item.request_id,
                scope=_run_scope_summary(session, item.config_snapshot),
            )
            for item in runs[start : start + page_size]
        ],
        total=len(runs),
        page=page,
        page_size=page_size,
    )


async def retrieval_debug(
    session: Session,
    rag_service: RAGService,
    user: User,
    data: RetrievalDebugRequest,
) -> RetrievalDebugResponse:
    _validate_knowledge_bases(session, user, data.knowledge_base_ids)
    result = await rag_service.retrieve(
        RetrievalRequest(
            query=data.query,
            knowledge_base_ids=data.knowledge_base_ids,
            strategy=data.strategy,
            top_k=data.top_k,
            rerank_enabled=data.rerank_enabled,
            lightrag_query_mode=data.query_mode,
        )
    )
    return RetrievalDebugResponse(
        query=data.query,
        strategy=data.strategy,
        lightrag_query_mode=data.query_mode,
        rerank_applied=result.rerank_applied,
        items=result.trace,
        warnings=result.warnings,
    )
