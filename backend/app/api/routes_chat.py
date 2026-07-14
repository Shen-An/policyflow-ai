"""Chat and conversation API routes."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.exceptions import ApplicationError
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
    iter_chat_events,
    list_conversations,
    rename_conversation,
    send_chat_message,
)

router = APIRouter(tags=["chat"])


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/api/chat", response_model=ChatResponse)
async def post_chat(
    data: ChatRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ChatResponse:
    return await send_chat_message(
        session,
        user,
        data,
        request.app.state.agent_pipeline,
        memory_agent=getattr(request.app.state, "memory_agent", None),
        tool_registry=getattr(request.app.state, "tool_registry", None),
        skill_registry=getattr(request.app.state, "skill_registry", None),
        rag_service=getattr(request.app.state, "rag_service", None),
    )


@router.post("/api/chat/stream")
async def post_chat_stream(
    data: ChatRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> StreamingResponse:
    """Stream thinking stages (memory/tools/commands) then the final answer as SSE."""

    async def event_generator():
        try:
            async for event_name, payload in iter_chat_events(
                session,
                user,
                data,
                request.app.state.agent_pipeline,
                memory_agent=getattr(request.app.state, "memory_agent", None),
                tool_registry=getattr(request.app.state, "tool_registry", None),
                skill_registry=getattr(request.app.state, "skill_registry", None),
                rag_service=getattr(request.app.state, "rag_service", None),
            ):
                yield _sse(event_name, payload if isinstance(payload, dict) else {"value": payload})
        except ApplicationError as exc:
            yield _sse(
                "error",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "status_code": exc.status_code,
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures to client stream
            yield _sse(
                "error",
                {
                    "code": "CHAT_STREAM_ERROR",
                    "message": f"流式问答失败：{type(exc).__name__}",
                    "status_code": 500,
                },
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
