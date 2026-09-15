"""User memory management API routes."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from backend.app.api.deps import CurrentUser, SessionDep, UnitOfWorkDep
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
    carries that tenant. The legacy synchronous list, which filtered by owner alone
    and could therefore show one tenant another's rows, is no longer reachable from
    this route.

    The type vocabulary is passed in rather than imported by the repository, which
    keeps the data layer free of service-level knowledge.
    """
    items, total = await uow.memories.list_for_user(
        user.tenant_id or "",
        user.id,
        allowed_types=MANAGEABLE_TYPES,
        page=page,
        page_size=page_size,
        memory_type=memory_type,
        keyword=keyword,
        include_expired=include_expired,
    )
    return MemoryListResponse(
        items=[MemoryItemRead.model_validate(to_memory_read(item)) for item in items],
        total=total,
        page=max(page, 1),
        page_size=min(max(page_size, 1), 100),
    )


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_memory_route(
    memory_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    """Delete a memory owned by the current user.

    This handler still runs on the legacy synchronous path. The repository delete
    is owner-and-tenant qualified but not yet conversation-aware, and the legacy
    handler accepts deleting a conversation-scoped memory of one's own
    conversation; moving this route would silently drop that case. It follows once
    the repository covers it.
    """
    delete_user_memory(session, user.id, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
