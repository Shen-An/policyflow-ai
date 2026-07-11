"""Evaluation case, run, and retrieval-debug API routes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from backend.app.agents.pipeline import AgentPipeline
from backend.app.api.deps import SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.eval import (
    EvalCaseCreate,
    EvalCaseRead,
    EvalRunCreate,
    EvalRunListResponse,
    EvalRunRead,
    RetrievalDebugRequest,
    RetrievalDebugResponse,
    RetrievalEvalItemCreate,
    RetrievalEvalItemRead,
)
from backend.app.services.eval_service import (
    create_eval_case,
    create_eval_run,
    create_retrieval_item,
    execute_eval_run,
    get_eval_run,
    list_eval_cases,
    list_eval_runs,
    list_retrieval_items,
    retrieval_debug,
)
from backend.app.services.rag_service import RAGService

router = APIRouter(prefix="/api/eval", tags=["evaluation"])
EvalAdmin = Annotated[User, Depends(require_roles("kb_admin", "sys_admin"))]


@router.post("/cases", response_model=EvalCaseRead, status_code=status.HTTP_201_CREATED)
def post_eval_case(
    data: EvalCaseCreate,
    session: SessionDep,
    _: EvalAdmin,
) -> EvalCaseRead:
    return create_eval_case(session, data)


@router.get("/cases", response_model=list[EvalCaseRead])
def get_eval_cases(
    session: SessionDep,
    _: EvalAdmin,
    category: str | None = None,
) -> list[EvalCaseRead]:
    return list_eval_cases(session, category)


@router.post(
    "/retrieval-items",
    response_model=RetrievalEvalItemRead,
    status_code=status.HTTP_201_CREATED,
)
def post_retrieval_item(
    data: RetrievalEvalItemCreate,
    session: SessionDep,
    user: EvalAdmin,
) -> RetrievalEvalItemRead:
    return create_retrieval_item(session, user, data)


@router.get("/retrieval-items", response_model=list[RetrievalEvalItemRead])
def get_retrieval_items(
    session: SessionDep,
    _: EvalAdmin,
    enabled: bool | None = None,
) -> list[RetrievalEvalItemRead]:
    return list_retrieval_items(session, enabled)


@router.post("/runs", response_model=EvalRunRead, status_code=status.HTTP_201_CREATED)
async def post_eval_run(
    data: EvalRunCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: EvalAdmin,
    session: SessionDep,
) -> EvalRunRead:
    rag_service: RAGService = request.app.state.rag_service
    pipeline: AgentPipeline = request.app.state.agent_pipeline
    eval_run = create_eval_run(
        session,
        rag_service,
        user,
        data,
        getattr(request.state, "request_id", None),
    )
    background_tasks.add_task(
        execute_eval_run,
        request.app.state.engine,
        rag_service,
        pipeline,
        eval_run.id,
        data,
    )
    return eval_run


@router.get("/runs", response_model=EvalRunListResponse)
def get_eval_runs(
    session: SessionDep,
    _: EvalAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: str | None = Query(default=None, alias="status"),
    created_by: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> EvalRunListResponse:
    return list_eval_runs(
        session,
        page,
        page_size,
        status_filter,
        str(created_by) if created_by else None,
        created_from,
        created_to,
    )


@router.get("/runs/{run_id}", response_model=EvalRunRead)
def get_eval_run_route(
    run_id: UUID,
    session: SessionDep,
    _: EvalAdmin,
) -> EvalRunRead:
    return get_eval_run(session, str(run_id))


@router.post("/retrieval-debug", response_model=RetrievalDebugResponse)
async def post_retrieval_debug(
    data: RetrievalDebugRequest,
    request: Request,
    user: EvalAdmin,
    session: SessionDep,
) -> RetrievalDebugResponse:
    rag_service: RAGService = request.app.state.rag_service
    return await retrieval_debug(session, rag_service, user, data)
