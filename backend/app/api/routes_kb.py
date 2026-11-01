"""Knowledge-base and document API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
    status,
)
from starlette.concurrency import run_in_threadpool

from backend.app.api.deps import CurrentUser, SessionDep, UnitOfWorkDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.jobs.runner import submit_document_index
from backend.app.schemas.knowledge import (
    DepartmentListResponse,
    DocumentDeleteResponse,
    DocumentDetail,
    DocumentListResponse,
    DocumentStatusResponse,
    DocumentUpdate,
    DocumentUploadResponse,
    IndexJobResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseCreateOptions,
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseListResponse,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from backend.app.services.document_service import (
    create_index_job,
    delete_document,
    get_document_detail,
    get_document_status,
    list_documents,
    update_document,
    upload_document,
)
from backend.app.services.knowledge_base_service import (
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base_create_options_async,
    get_knowledge_base_detail_async,
    list_departments_async,
    list_knowledge_bases_async,
    update_knowledge_base,
)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])
documents_router = APIRouter(prefix="/api/documents", tags=["documents"])
departments_router = APIRouter(prefix="/api/departments", tags=["departments"])
KnowledgeAdmin = Annotated[User, Depends(require_roles("kb_admin", "sys_admin"))]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
def post_knowledge_base(
    data: KnowledgeBaseCreate,
    request: Request,
    session: SessionDep,
    user: KnowledgeAdmin,
) -> KnowledgeBaseRead:
    return create_knowledge_base(
        session,
        request.app.state.settings,
        user,
        data,
        _client_ip(request),
    )


# These four are read-only, so they run on the async unit of work: a sync
# ``Session`` here would hold a thread of the pool for the whole request and,
# worse, invite the next author to add a write to a path that has no transaction
# of its own. Writes below still use the synchronous session on purpose.
@router.get("", response_model=KnowledgeBaseListResponse)
async def get_knowledge_bases(
    user: CurrentUser,
    uow: UnitOfWorkDep,
) -> KnowledgeBaseListResponse:
    return await list_knowledge_bases_async(uow, user)


@departments_router.get("", response_model=DepartmentListResponse)
async def get_departments(_: CurrentUser, uow: UnitOfWorkDep) -> DepartmentListResponse:
    return await list_departments_async(uow)


@router.get("/create-options", response_model=KnowledgeBaseCreateOptions)
async def get_create_options(
    _: KnowledgeAdmin,
    uow: UnitOfWorkDep,
) -> KnowledgeBaseCreateOptions:
    return await get_knowledge_base_create_options_async(uow)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    user: CurrentUser,
    uow: UnitOfWorkDep,
) -> KnowledgeBaseRead:
    return await get_knowledge_base_detail_async(uow, user, str(knowledge_base_id))


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
@router.put("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def put_knowledge_base(
    knowledge_base_id: UUID,
    data: KnowledgeBaseUpdate,
    request: Request,
    session: SessionDep,
    user: KnowledgeAdmin,
) -> KnowledgeBaseRead:
    return update_knowledge_base(
        session,
        user,
        str(knowledge_base_id),
        data,
        _client_ip(request),
    )


@router.delete("/{knowledge_base_id}", response_model=KnowledgeBaseDeleteResponse)
def remove_knowledge_base(
    knowledge_base_id: UUID,
    request: Request,
    session: SessionDep,
    user: KnowledgeAdmin,
) -> KnowledgeBaseDeleteResponse:
    return delete_knowledge_base(
        session,
        user,
        str(knowledge_base_id),
        _client_ip(request),
    )


@router.post(
    "/{knowledge_base_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_document(
    knowledge_base_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
) -> DocumentUploadResponse:
    response = await upload_document(
        session,
        request.app.state.settings,
        user,
        knowledge_base_id,
        file,
        title,
        _client_ip(request),
        # T086: the versioned storage authority when the material pipeline is
        # wired on this process, otherwise the instrumented legacy adapter. The
        # service resolves which; the route only supplies what is available.
        pipeline=getattr(request.app.state, "material_saga", None),
        telemetry=getattr(request.app.state, "storage_authority_telemetry", None),
    )
    # T070: durable submission replaces the ephemeral BackgroundTask. The index
    # job id keys idempotency, so a redelivered submit of this attempt dedups
    # while a later re-index (a new RagIndexJob) enqueues its own durable job.
    await submit_document_index(
        app=request.app,
        background_tasks=background_tasks,
        tenant_id=getattr(user, "tenant_id", ""),
        document_id=response.document_id,
        idempotency_key=response.index_job_id,
    )
    return response


@router.get("/{knowledge_base_id}/documents", response_model=DocumentListResponse)
def get_documents(
    knowledge_base_id: str,
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentListResponse:
    return list_documents(session, user, knowledge_base_id, page, page_size)


@documents_router.post("/{document_id}/index", response_model=IndexJobResponse)
async def post_document_index(
    document_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    session: SessionDep,
) -> IndexJobResponse:
    # ``create_index_job`` is still a synchronous service (it writes the
    # RagIndexJob, flips the document to pending, records the audit row and
    # commits), so it must not run on the event-loop thread: the commit is real
    # database I/O and parks every other request, including open chat SSE
    # streams, for its duration.
    response = await run_in_threadpool(
        create_index_job, session, user, document_id, _client_ip(request)
    )
    await submit_document_index(
        app=request.app,
        background_tasks=background_tasks,
        tenant_id=getattr(user, "tenant_id", ""),
        document_id=document_id,
        idempotency_key=response.job_id,
    )
    return response


@documents_router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> DocumentDetail:
    return get_document_detail(session, user, document_id)


@documents_router.patch("/{document_id}", response_model=DocumentDetail)
@documents_router.put("/{document_id}", response_model=DocumentDetail)
def put_document(
    document_id: str,
    data: DocumentUpdate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> DocumentDetail:
    return update_document(
        session,
        user,
        document_id,
        data,
        _client_ip(request),
    )


@documents_router.delete("/{document_id}", response_model=DocumentDeleteResponse)
def remove_document(
    document_id: str,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> DocumentDeleteResponse:
    return delete_document(
        session,
        user,
        document_id,
        _client_ip(request),
    )


@documents_router.get("/{document_id}/status", response_model=DocumentStatusResponse)
def get_document_index_status(
    document_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> DocumentStatusResponse:
    return get_document_status(session, user, document_id)
