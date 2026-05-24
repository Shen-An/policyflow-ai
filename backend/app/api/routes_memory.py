"""User memory management API routes."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.schemas.memory import MemoryItemRead, MemoryListResponse
from backend.app.services.memory_service import (
    delete_user_memory,
    list_user_memories,
    to_memory_read,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
def list_memories_route(
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    memory_type: Annotated[str | None, Query(max_length=50)] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    include_expired: bool = False,
) -> MemoryListResponse:
    """List the current user's memories (preferences, LTM, entities, trails)."""
    items, total = list_user_memories(
        session,
        user.id,
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
    """Delete a memory owned by the current user."""
    delete_user_memory(session, user.id, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
