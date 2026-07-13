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

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.schemas.knowledge import (
    DepartmentListResponse,
    DocumentDetail,
    DocumentListResponse,
    DocumentStatusResponse,
    DocumentUploadResponse,
    IndexJobResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseCreateOptions,
    KnowledgeBaseListResponse,
    KnowledgeBaseRead,
)
from backend.app.services.document_service import (
    create_index_job,
    get_document_detail,
    get_document_status,
    list_documents,
    upload_document,
)
from backend.app.services.indexing_service import process_document_index
from backend.app.services.knowledge_base_service import (
    create_knowledge_base,
    get_knowledge_base_create_options,
    get_knowledge_base_detail,
    list_departments,
    list_knowledge_bases,
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


@router.get("", response_model=KnowledgeBaseListResponse)
def get_knowledge_bases(user: CurrentUser, session: SessionDep) -> KnowledgeBaseListResponse:
    return list_knowledge_bases(session, user)


@departments_router.get("", response_model=DepartmentListResponse)
def get_departments(_: CurrentUser, session: SessionDep) -> DepartmentListResponse:
    return list_departments(session)


@router.get("/create-options", response_model=KnowledgeBaseCreateOptions)
def get_create_options(
    _: KnowledgeAdmin,
    session: SessionDep,
) -> KnowledgeBaseCreateOptions:
    return get_knowledge_base_create_options(session)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def get_knowledge_base(
    knowledge_base_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeBaseRead:
    return get_knowledge_base_detail(session, user, str(knowledge_base_id))


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
    )
    background_tasks.add_task(
        process_document_index,
        request.app.state.engine,
        request.app.state.lightrag_adapter,
        response.document_id,
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
def post_document_index(
    document_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    session: SessionDep,
) -> IndexJobResponse:
    response = create_index_job(session, user, document_id, _client_ip(request))
    background_tasks.add_task(
        process_document_index,
        request.app.state.engine,
        request.app.state.lightrag_adapter,
        document_id,
    )
    return response


@documents_router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> DocumentDetail:
    return get_document_detail(session, user, document_id)


@documents_router.get("/{document_id}/status", response_model=DocumentStatusResponse)
def get_document_index_status(
    document_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> DocumentStatusResponse:
    return get_document_status(session, user, document_id)
