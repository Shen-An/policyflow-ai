"""Chat and conversation API routes."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationListResponse,
    ConversationRead,
    ConversationSummary,
    ConversationUpdate,
)
from backend.app.services.chat_service import (
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    send_chat_message,
)

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def post_chat(
    data: ChatRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ChatResponse:
    return await send_chat_message(session, user, data, request.app.state.agent_pipeline)


@router.get("/api/conversations", response_model=ConversationListResponse)
def list_conversations_route(
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
) -> ConversationListResponse:
    """List conversations owned by the authenticated user only."""
    return list_conversations(
        session,
        user,
        page=page,
        page_size=page_size,
        keyword=keyword,
    )


@router.get("/api/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation_route(
    conversation_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationRead:
    return get_conversation(session, user, conversation_id)


@router.patch("/api/conversations/{conversation_id}", response_model=ConversationSummary)
def patch_conversation_route(
    conversation_id: str,
    data: ConversationUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationSummary:
    """Rename a conversation owned by the current user."""
    return rename_conversation(session, user, conversation_id, data.title)


@router.delete(
    "/api/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_conversation_route(
    conversation_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    """Soft-delete a conversation owned by the current user."""
    delete_conversation(session, user, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
