"""Chat and conversation API routes."""

from fastapi import APIRouter, Request

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.schemas.chat import ChatRequest, ChatResponse, ConversationRead
from backend.app.services.chat_service import get_conversation, send_chat_message

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def post_chat(
    data: ChatRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ChatResponse:
    return await send_chat_message(session, user, data, request.app.state.agent_pipeline)


@router.get("/api/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation_route(
    conversation_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationRead:
    return get_conversation(session, user, conversation_id)
