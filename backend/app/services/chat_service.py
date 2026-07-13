"""Chat persistence and Agent Pipeline orchestration."""

import json
from time import perf_counter

from sqlmodel import Session, col, select

from backend.app.agents.memory_agent import MemoryAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import AIQueryLog, Conversation, KnowledgeBase, Message, User, utc_now
from backend.app.schemas.chat import (
    AssistantMessageMetadata,
    ChatRequest,
    ChatResponse,
    Citation,
    ConversationListResponse,
    ConversationRead,
    ConversationSummary,
    MessageRead,
)
from backend.app.schemas.retrieval import RetrievalRequest
from backend.app.services.knowledge_base_router import select_candidate_knowledge_bases
from backend.app.services.permission_service import get_knowledge_base_permission
from backend.app.services.user_service import get_user_role_codes


def _authorized_knowledge_bases(
    session: Session,
    user: User,
    requested_ids: list[str],
) -> list[KnowledgeBase]:
    knowledge_bases = session.exec(
        select(KnowledgeBase).where(KnowledgeBase.status == "active")
    ).all()
    requested = set(requested_ids)
    return [
        knowledge_base
        for knowledge_base in knowledge_bases
        if (not requested or knowledge_base.id in requested)
        and get_knowledge_base_permission(session, user, knowledge_base) is not None
    ]


def _conversation(
    session: Session, user: User, conversation_id: str | None, question: str
) -> Conversation:
    if conversation_id is None:
        conversation = Conversation(user_id=user.id, title=question.strip()[:100])
        session.add(conversation)
        session.flush()
        return conversation
    existing_conversation = session.get(Conversation, conversation_id)
    if existing_conversation is None or existing_conversation.status == "deleted":
        raise ApplicationError("CONVERSATION_NOT_FOUND", "Conversation not found", 404)
    role_codes = get_user_role_codes(session, user.id)
    if existing_conversation.user_id != user.id and "sys_admin" not in role_codes:
        raise ApplicationError("PERMISSION_DENIED", "Conversation access denied", 403)
    return existing_conversation


def _owned_conversation(session: Session, user: User, conversation_id: str) -> Conversation:
    """Resolve a conversation that the current user owns and can manage."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.status == "deleted":
        raise ApplicationError("CONVERSATION_NOT_FOUND", "Conversation not found", 404)
    if conversation.user_id != user.id:
        raise ApplicationError("PERMISSION_DENIED", "Conversation access denied", 403)
    return conversation


async def send_chat_message(
    session: Session,
    user: User,
    data: ChatRequest,
    pipeline: AgentPipeline,
) -> ChatResponse:
    started_at = perf_counter()
    conversation = _conversation(session, user, data.conversation_id, data.question)
    session.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=data.question,
        )
    )
    authorized_knowledge_bases = _authorized_knowledge_bases(session, user, data.knowledge_base_ids)
    knowledge_bases = select_candidate_knowledge_bases(data.question, authorized_knowledge_bases)
    retrieval_request = (
        RetrievalRequest(
            query=data.question,
            knowledge_base_ids=[knowledge_base.id for knowledge_base in knowledge_bases],
            strategy=data.retrieval_strategy,
            top_k=data.top_k,
            rerank_enabled=data.rerank_enabled,
            lightrag_query_mode=data.query_mode,
        )
        if knowledge_bases
        else None
    )
    pipeline_result = await pipeline.run(
        data.question, knowledge_bases, retrieval_request, data.enable_skill
    )
    citations = [
        Citation(
            knowledge_base_id=evidence.knowledge_base_id,
            knowledge_base_name=evidence.knowledge_base_name,
            document_id=evidence.document_id,
            document_title=evidence.document_title,
            chunk_id=evidence.chunk_id,
            snippet=evidence.snippet,
            score=evidence.score,
        )
        for evidence in pipeline_result.retrieval_result.evidence
    ]
    latency_ms = int((perf_counter() - started_at) * 1000)
    query_log = AIQueryLog(
        conversation_id=conversation.id,
        user_id=user.id,
        question=data.question,
        answer=pipeline_result.answer_result.answer,
        knowledge_base_ids=[knowledge_base.id for knowledge_base in knowledge_bases],
        retrieved_sources=[
            trace.model_dump(mode="json") for trace in pipeline_result.retrieval_result.trace
        ],
        confidence_score=pipeline_result.answer_result.confidence_score,
        query_mode=data.query_mode.value,
        latency_ms=latency_ms,
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=pipeline_result.answer_result.answer,
        meta_json=AssistantMessageMetadata(
            citations=citations,
            query_log_id=query_log.id,
            confidence_score=pipeline_result.answer_result.confidence_score,
            query_mode=data.query_mode.value,
            router_result=pipeline_result.router_result,
            suggested_skills=pipeline_result.suggested_skills,
            compliance=pipeline_result.compliance,
        ).model_dump(mode="json"),
    )
    session.add(assistant_message)
    conversation.updated_at = utc_now()
    session.add(query_log)
    session.add(conversation)
    session.commit()
    session.refresh(assistant_message)
    session.refresh(query_log)
    MemoryAgent().run(
        session,
        conversation.id,
        data.question,
        pipeline_result.answer_result.answer,
    )
    return ChatResponse(
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        query_log_id=query_log.id,
        answer=pipeline_result.answer_result.answer,
        citations=citations,
        confidence_score=pipeline_result.answer_result.confidence_score,
        query_mode=data.query_mode.value,
        router_result=pipeline_result.router_result,
        compliance=pipeline_result.compliance,
        suggested_skills=pipeline_result.suggested_skills,
    )


def get_conversation(session: Session, user: User, conversation_id: str) -> ConversationRead:
    conversation = _conversation(session, user, conversation_id, "")
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(col(Message.created_at))
    ).all()
    try:
        summary = json.loads(conversation.summary) if conversation.summary else {}
    except json.JSONDecodeError:
        summary = {}
    return ConversationRead(
        id=conversation.id,
        title=conversation.title,
        status=conversation.status,
        summary=summary,
        messages=[
            MessageRead(
                id=message.id,
                role=message.role,
                content=message.content,
                meta_json=AssistantMessageMetadata.model_validate(message.meta_json),
                created_at=message.created_at,
            )
            for message in messages
        ],
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def list_conversations(
    session: Session,
    user: User,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
) -> ConversationListResponse:
    """Return conversations owned by the current user only.

    Even sys_admin does not see other users' history here; admin access to a
    specific conversation remains available via get_conversation.
    """
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), 100)
    conversations = session.exec(
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.status != "deleted",
        )
        .order_by(col(Conversation.updated_at).desc())
    ).all()

    normalized_keyword = (keyword or "").strip().lower()
    filtered: list[Conversation] = []
    for conversation in conversations:
        if not normalized_keyword:
            filtered.append(conversation)
            continue
        haystacks = [conversation.title.lower()]
        latest = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(col(Message.created_at).desc())
        ).first()
        if latest is not None:
            haystacks.append(latest.content.lower())
        if any(normalized_keyword in value for value in haystacks):
            filtered.append(conversation)

    total = len(filtered)
    start = (safe_page - 1) * safe_page_size
    page_items = filtered[start : start + safe_page_size]

    items: list[ConversationSummary] = []
    for conversation in page_items:
        messages = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(col(Message.created_at).desc())
        ).all()
        last_message = messages[0] if messages else None
        preview = None
        if last_message is not None:
            preview = " ".join(last_message.content.split())
            if len(preview) > 80:
                preview = f"{preview[:80]}…"
        items.append(
            ConversationSummary(
                id=conversation.id,
                title=conversation.title,
                status=conversation.status,
                message_count=len(messages),
                last_message_preview=preview,
                last_message_role=last_message.role if last_message is not None else None,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
        )

    return ConversationListResponse(
        items=items,
        total=total,
        page=safe_page,
        page_size=safe_page_size,
    )


def rename_conversation(
    session: Session,
    user: User,
    conversation_id: str,
    title: str,
) -> ConversationSummary:
    conversation = _owned_conversation(session, user, conversation_id)
    cleaned = title.strip()
    if not cleaned:
        raise ApplicationError("VALIDATION_ERROR", "Conversation title is required", 422)
    conversation.title = cleaned[:255]
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(col(Message.created_at).desc())
    ).all()
    last_message = messages[0] if messages else None
    preview = None
    if last_message is not None:
        preview = " ".join(last_message.content.split())
        if len(preview) > 80:
            preview = f"{preview[:80]}…"
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        status=conversation.status,
        message_count=len(messages),
        last_message_preview=preview,
        last_message_role=last_message.role if last_message is not None else None,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def delete_conversation(session: Session, user: User, conversation_id: str) -> None:
    conversation = _owned_conversation(session, user, conversation_id)
    conversation.status = "deleted"
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()
