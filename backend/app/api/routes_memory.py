"""User memory management API routes."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from backend.app.api.deps import CurrentUser, UnitOfWorkDep
from backend.app.db.repositories import require_tenant
from backend.app.schemas.memory import MemoryItemRead, MemoryListResponse
from backend.app.services.memory_service import (
    MANAGEABLE_TYPES,
    delete_user_memory,
    to_memory_read,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
async def list_memories_route(
    user: CurrentUser,
    uow: UnitOfWorkDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    memory_type: Annotated[str | None, Query(max_length=50)] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    include_expired: bool = False,
) -> MemoryListResponse:
    """List the current user's memories (preferences, LTM, entities, trails).

    The read runs on the tenant-qualified asynchronous repository: the tenant comes
    from the account rather than from the request, and every query it issues
    carries that tenant. The legacy synchronous list filtered by owner alone and
    so could show one tenant another's rows - that path is no longer reachable.

    The type vocabulary is passed into the repository rather than imported by it,
    which keeps the data layer free of service-level knowledge.
    """
    items, total = await uow.memories.list_for_user(
        require_tenant(user.tenant_id, "list_memories_route"),
        user.id,
        allowed_types=MANAGEABLE_TYPES,
        page=page,
        page_size=page_size,
        memory_type=memory_type,
        keyword=keyword,
        include_expired=include_expired,
    )
    safe_page = max(page, 1)
    safe_size = min(max(page_size, 1), 100)
    return MemoryListResponse(
        items=[MemoryItemRead.model_validate(to_memory_read(item)) for item in items],
        total=total,
        page=safe_page,
        page_size=safe_size,
    )


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_memory_route(
    memory_id: str,
    user: CurrentUser,
    uow: UnitOfWorkDep,
) -> Response:
    """Delete a memory the current user may manage.

    The delete runs on the tenant-qualified asynchronous repository. It is
    conversation-aware: the member may remove a memory they own directly or a
    conversation-scoped memory belonging to one of their own conversations, and a
    memory outside the tenant reads as not-found rather than forbidden, so the
    response cannot be used to probe another tenant's ids.
    """
    await delete_user_memory(
        uow.memories,
        require_tenant(user.tenant_id, "delete_memory_route"),
        user.id,
        memory_id,
    )
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
