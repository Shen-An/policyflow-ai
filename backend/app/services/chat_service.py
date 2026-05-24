"""Chat persistence and Agent Pipeline orchestration."""

import json
from time import perf_counter
from typing import Any

from sqlmodel import Session, col, select

from backend.app.agents.base import MemoryWorkingSet
from backend.app.agents.memory_agent import MemoryAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.core.config import get_settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    AIQueryLog,
    Conversation,
    KnowledgeBase,
    Message,
    ToolCallLog,
    User,
    utc_now,
)
from backend.app.schemas.chat import (
    AssistantMessageMetadata,
    ChatRequest,
    ChatResponse,
    Citation,
    CommandTrace,
    ConversationListResponse,
    ConversationRead,
    ConversationSummary,
    MessageRead,
    ToolCallTrace,
    TurnDiagnostics,
    UsedMemoryItem,
)
from backend.app.schemas.retrieval import RetrievalRequest
from backend.app.services.knowledge_base_router import select_candidate_knowledge_bases
from backend.app.services.permission_service import get_knowledge_base_permission
from backend.app.services.query_rewrite import expand_retrieval_query
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


def _clip(text: str, limit: int = 240) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


def _memory_items_from_working_set(working_set: MemoryWorkingSet | None) -> list[UsedMemoryItem]:
    if working_set is None:
        return []
    items: list[UsedMemoryItem] = []
    for raw in working_set.fixed_memories:
        items.append(
            UsedMemoryItem(
                id=str(raw.get("id") or "") or None,
                memory_type=str(raw.get("type") or "unknown"),
                content=_clip(str(raw.get("content") or "")),
                source_slot="fixed",
                confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
            )
        )
    for raw in working_set.recalled_memories:
        items.append(
            UsedMemoryItem(
                id=str(raw.get("id") or "") or None,
                memory_type=str(raw.get("type") or "unknown"),
                content=_clip(str(raw.get("content") or "")),
                source_slot="recalled",
                confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
            )
        )
    if working_set.rolling_summary:
        items.append(
            UsedMemoryItem(
                id=None,
                memory_type="stm_summary",
                content=_clip(working_set.rolling_summary, 320),
                source_slot="rolling_summary",
                confidence=None,
            )
        )
    if working_set.history:
        preview = " / ".join(
            f"{item.get('role')}:{_clip(str(item.get('content') or ''), 40)}"
            for item in working_set.history[-4:]
        )
        items.append(
            UsedMemoryItem(
                id=None,
                memory_type="conversation_history",
                content=_clip(f"最近 {len(working_set.history)} 条：{preview}", 320),
                source_slot="history",
                confidence=None,
            )
        )
    return items


def _collect_tool_traces(
    session: Session,
    *,
    conversation_id: str,
    user_id: str,
    since: Any,
) -> list[ToolCallTrace]:
    logs = session.exec(
        select(ToolCallLog)
        .where(
            ToolCallLog.conversation_id == conversation_id,
            ToolCallLog.user_id == user_id,
        )
        .order_by(col(ToolCallLog.created_at).desc())
        .limit(20)
    ).all()
    traces: list[ToolCallTrace] = []
    for log in logs:
        if since is not None and log.created_at is not None and log.created_at < since:
            continue
        traces.append(
            ToolCallTrace(
                tool_name=log.tool_name,
                status=log.status,
                agent_name=log.agent_name,
                input_summary=dict(log.input_summary or {}),
                output_summary=dict(log.output_summary or {}),
                error_message=log.error_message,
                latency_ms=log.latency_ms,
            )
        )
    traces.reverse()
    return traces


def _build_command_traces(
    pipeline_result: Any,
    knowledge_bases: list[KnowledgeBase],
    *,
    has_working_set: bool,
) -> list[CommandTrace]:
    commands: list[CommandTrace] = [
        CommandTrace(
            name="MemoryLoad",
            status="success" if has_working_set else "skipped",
            summary=(
                "加载固定偏好/实体、短期窗口与按需召回"
                if has_working_set
                else "本轮未启用记忆加载"
            ),
            output={"enabled": has_working_set},
        )
    ]
    router = pipeline_result.router_result
    commands.append(
        CommandTrace(
            name="RouterAgent",
            status="success",
            summary=f"domain={router.domain}, task={router.task_type}, risk={router.risk_level}",
            output=router.model_dump(mode="json"),
        )
    )
    evidence_count = len(pipeline_result.retrieval_result.evidence)
    commands.append(
        CommandTrace(
            name="RetrievalAgent",
            status="success" if evidence_count else "empty",
            summary=f"检索 {len(knowledge_bases)} 个知识库，命中 {evidence_count} 条证据",
            output={
                "knowledge_bases": [kb.name for kb in knowledge_bases],
                "evidence_count": evidence_count,
                "trace": [
                    item.model_dump(mode="json")
                    for item in pipeline_result.retrieval_result.trace
                ][:10],
            },
        )
    )
    commands.append(
        CommandTrace(
            name="AnswerAgent",
            status="success",
            summary=_clip(pipeline_result.answer_result.answer, 160),
            output={
                "confidence_score": pipeline_result.answer_result.confidence_score,
                "answer_preview": _clip(pipeline_result.answer_result.answer, 400),
            },
        )
    )
    skills = pipeline_result.suggested_skills or []
    commands.append(
        CommandTrace(
            name="SkillAgent",
            status="success" if skills else "empty",
            summary=(
                "建议 Skill：" + "、".join(item.get("name", "") for item in skills)
                if skills
                else "无 Skill 建议"
            ),
            output={"suggested_skills": skills},
        )
    )
    compliance = pipeline_result.compliance
    commands.append(
        CommandTrace(
            name="ComplianceAgent",
            status="success" if compliance.passed else "warning",
            summary=(
                "合规通过"
                if compliance.passed
                else "合规告警：" + "、".join(compliance.warnings)
            ),
            output=compliance.model_dump(mode="json"),
        )
    )
    commands.append(
        CommandTrace(
            name="MemoryWriteback",
            status="success" if has_working_set else "skipped",
            summary=(
                "抽取事件并写入长期/实体/偏好记忆"
                if has_working_set
                else "本轮未启用记忆回写"
            ),
            output={"enabled": has_working_set},
        )
    )
    return commands


def _build_diagnostics(
    session: Session,
    *,
    conversation: Conversation,
    user: User,
    working_set: MemoryWorkingSet | None,
    pipeline_result: Any,
    knowledge_bases: list[KnowledgeBase],
    turn_started_at: Any,
) -> TurnDiagnostics:
    memories = _memory_items_from_working_set(working_set)
    tools = _collect_tool_traces(
        session,
        conversation_id=conversation.id,
        user_id=user.id,
        since=turn_started_at,
    )
    if not tools and pipeline_result.suggested_skills:
        for skill in pipeline_result.suggested_skills:
            tools.append(
                ToolCallTrace(
                    tool_name=f"skill.suggest:{skill.get('name', 'unknown')}",
                    status="suggested",
                    agent_name="SkillAgent",
                    input_summary={},
                    output_summary={
                        "name": skill.get("name"),
                        "description": skill.get("description"),
                    },
                    latency_ms=0,
                )
            )
    commands = _build_command_traces(
        pipeline_result,
        knowledge_bases,
        has_working_set=working_set is not None,
    )
    return TurnDiagnostics(memories=memories, tools=tools, commands=commands)


async def send_chat_message(
    session: Session,
    user: User,
    data: ChatRequest,
    pipeline: AgentPipeline,
    memory_agent: MemoryAgent | None = None,
) -> ChatResponse:
    final: ChatResponse | None = None
    async for event_name, payload in iter_chat_events(
        session,
        user,
        data,
        pipeline,
        memory_agent=memory_agent,
    ):
        if event_name == "final":
            final = ChatResponse.model_validate(payload)
        elif event_name == "error":
            raise ApplicationError(
                str(payload.get("code") or "CHAT_ERROR"),
                str(payload.get("message") or "Chat failed"),
                int(payload.get("status_code") or 500),
            )
    if final is None:
        raise ApplicationError("CHAT_ERROR", "Chat produced no final response", 500)
    return final


async def iter_chat_events(
    session: Session,
    user: User,
    data: ChatRequest,
    pipeline: AgentPipeline,
    memory_agent: MemoryAgent | None = None,
):
    """Yield (event_name, payload) stages for SSE streaming."""
    from collections.abc import AsyncIterator

    started_at = perf_counter()
    turn_started_at = utc_now()
    conversation = _conversation(session, user, data.conversation_id, data.question)
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=data.question,
    )
    session.add(user_message)
    session.flush()

    yield (
        "stage",
        {
            "stage": "MemoryLoad",
            "status": "running",
            "message": "正在加载固定偏好、实体与短期记忆…",
        },
    )
    working_set = None
    if memory_agent is not None:
        working_set = await memory_agent.load(session, user, conversation, data.question)
    memory_items = _memory_items_from_working_set(working_set)
    yield (
        "stage",
        {
            "stage": "MemoryLoad",
            "status": "success" if working_set is not None else "skipped",
            "message": (
                f"已加载记忆 {len(memory_items)} 条"
                if working_set is not None
                else "本轮未启用记忆加载"
            ),
        },
    )
    yield (
        "diagnostics_partial",
        {
            "memories": [item.model_dump(mode="json") for item in memory_items],
            "tools": [],
            "commands": [
                {
                    "name": "MemoryLoad",
                    "status": "success" if working_set is not None else "skipped",
                    "summary": (
                        f"已加载记忆 {len(memory_items)} 条"
                        if working_set is not None
                        else "本轮未启用记忆加载"
                    ),
                    "output": {"enabled": working_set is not None, "count": len(memory_items)},
                }
            ],
        },
    )

    authorized_knowledge_bases = _authorized_knowledge_bases(
        session, user, data.knowledge_base_ids
    )
    history_for_rewrite = working_set.history if working_set is not None else []
    rolling_summary = working_set.rolling_summary if working_set is not None else ""
    retrieval_query = expand_retrieval_query(
        data.question,
        history=history_for_rewrite,
        rolling_summary=rolling_summary,
    )
    knowledge_bases = select_candidate_knowledge_bases(
        retrieval_query,
        authorized_knowledge_bases,
    )
    retrieval_request = (
        RetrievalRequest(
            query=retrieval_query,
            knowledge_base_ids=[knowledge_base.id for knowledge_base in knowledge_bases],
            strategy=data.retrieval_strategy,
            top_k=data.top_k,
            rerank_enabled=data.rerank_enabled,
            lightrag_query_mode=data.query_mode,
        )
        if knowledge_bases
        else None
    )

    if retrieval_query != data.question.strip():
        yield (
            "stage",
            {
                "stage": "QueryRewrite",
                "status": "success",
                "message": f"已结合上下文扩写检索：{retrieval_query[:80]}",
            },
        )

    from backend.app.agents.base import PipelineResult
    from backend.app.schemas.retrieval import RetrievalResult

    yield (
        "stage",
        {"stage": "RouterAgent", "status": "running", "message": "正在分析问题领域与风险…"},
    )
    router_result = await pipeline.router_agent.run(data.question, knowledge_bases)
    yield (
        "stage",
        {
            "stage": "RouterAgent",
            "status": "success",
            "message": f"domain={router_result.domain}, risk={router_result.risk_level}",
        },
    )
    yield (
        "diagnostics_partial",
        {
            "commands": [
                {
                    "name": "RouterAgent",
                    "status": "success",
                    "summary": (
                        f"domain={router_result.domain}, "
                        f"task={router_result.task_type}, risk={router_result.risk_level}"
                    ),
                    "output": router_result.model_dump(mode="json"),
                }
            ]
        },
    )

    yield (
        "stage",
        {"stage": "RetrievalAgent", "status": "running", "message": "正在检索授权知识库…"},
    )
    retrieval_result = (
        await pipeline.retrieval_agent.run(retrieval_request)
        if retrieval_request is not None
        else RetrievalResult(evidence=[], trace=[], latency_ms=0)
    )
    evidence_count = len(retrieval_result.evidence)
    yield (
        "stage",
        {
            "stage": "RetrievalAgent",
            "status": "success" if evidence_count else "empty",
            "message": f"检索 {len(knowledge_bases)} 个知识库，命中 {evidence_count} 条证据",
        },
    )
    yield (
        "diagnostics_partial",
        {
            "commands": [
                {
                    "name": "RetrievalAgent",
                    "status": "success" if evidence_count else "empty",
                    "summary": f"检索 {len(knowledge_bases)} 个知识库，命中 {evidence_count} 条证据",
                    "output": {
                        "knowledge_bases": [kb.name for kb in knowledge_bases],
                        "evidence_count": evidence_count,
                    },
                }
            ]
        },
    )

    yield (
        "stage",
        {"stage": "AnswerAgent", "status": "running", "message": "正在结合证据与记忆生成回答…"},
    )
    answer_result = await pipeline.answer_agent.run(
        data.question,
        retrieval_result.evidence,
        working_set,
    )
    yield (
        "stage",
        {"stage": "AnswerAgent", "status": "success", "message": "回答已生成"},
    )
    yield (
        "diagnostics_partial",
        {
            "commands": [
                {
                    "name": "AnswerAgent",
                    "status": "success",
                    "summary": _clip(answer_result.answer, 160),
                    "output": {
                        "confidence_score": answer_result.confidence_score,
                        "answer_preview": _clip(answer_result.answer, 400),
                    },
                }
            ]
        },
    )

    yield (
        "stage",
        {"stage": "SkillAgent", "status": "running", "message": "正在匹配可用 Skill…"},
    )
    suggested_skills = await pipeline.skill_agent.run(
        data.question, router_result, data.enable_skill
    )
    yield (
        "stage",
        {
            "stage": "SkillAgent",
            "status": "success" if suggested_skills else "empty",
            "message": (
                "建议：" + "、".join(item.get("name", "") for item in suggested_skills)
                if suggested_skills
                else "无 Skill 建议"
            ),
        },
    )
    tool_traces: list[dict[str, Any]] = []
    if suggested_skills:
        for skill in suggested_skills:
            tool_traces.append(
                {
                    "tool_name": f"skill.suggest:{skill.get('name', 'unknown')}",
                    "status": "suggested",
                    "agent_name": "SkillAgent",
                    "input_summary": {},
                    "output_summary": {
                        "name": skill.get("name"),
                        "description": skill.get("description"),
                    },
                    "error_message": None,
                    "latency_ms": 0,
                }
            )
    yield (
        "diagnostics_partial",
        {
            "tools": tool_traces,
            "commands": [
                {
                    "name": "SkillAgent",
                    "status": "success" if suggested_skills else "empty",
                    "summary": (
                        "建议 Skill：" + "、".join(item.get("name", "") for item in suggested_skills)
                        if suggested_skills
                        else "无 Skill 建议"
                    ),
                    "output": {"suggested_skills": suggested_skills},
                }
            ],
        },
    )

    yield (
        "stage",
        {"stage": "ComplianceAgent", "status": "running", "message": "正在做合规检查…"},
    )
    compliance = await pipeline.compliance_agent.run(
        answer_result.answer,
        retrieval_result.evidence,
    )
    yield (
        "stage",
        {
            "stage": "ComplianceAgent",
            "status": "success" if compliance.passed else "warning",
            "message": "合规通过" if compliance.passed else "存在合规告警",
        },
    )
    yield (
        "diagnostics_partial",
        {
            "commands": [
                {
                    "name": "ComplianceAgent",
                    "status": "success" if compliance.passed else "warning",
                    "summary": (
                        "合规通过"
                        if compliance.passed
                        else "合规告警：" + "、".join(compliance.warnings)
                    ),
                    "output": compliance.model_dump(mode="json"),
                }
            ]
        },
    )

    pipeline_result = PipelineResult(
        router_result=router_result,
        retrieval_result=retrieval_result,
        answer_result=answer_result,
        compliance=compliance,
        suggested_skills=suggested_skills,
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
    diagnostics = _build_diagnostics(
        session,
        conversation=conversation,
        user=user,
        working_set=working_set,
        pipeline_result=pipeline_result,
        knowledge_bases=knowledge_bases,
        turn_started_at=turn_started_at,
    )
    yield ("diagnostics", diagnostics.model_dump(mode="json"))

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
            diagnostics=diagnostics,
        ).model_dump(mode="json"),
    )
    session.add(assistant_message)
    conversation.updated_at = utc_now()
    session.add(query_log)
    session.add(conversation)
    session.commit()
    session.refresh(assistant_message)
    session.refresh(query_log)
    session.refresh(user_message)

    yield (
        "stage",
        {"stage": "MemoryWriteback", "status": "running", "message": "正在回写有价值记忆…"},
    )
    if memory_agent is not None:
        try:
            session.refresh(conversation)
            await memory_agent.writeback(
                session,
                user,
                conversation,
                data.question,
                pipeline_result.answer_result.answer,
                source_message_ids=[user_message.id, assistant_message.id],
            )
            yield (
                "stage",
                {
                    "stage": "MemoryWriteback",
                    "status": "success",
                    "message": "记忆回写完成",
                },
            )
        except Exception as exc:
            from backend.app.core.logging import get_logger

            get_logger(__name__).warning(
                "Memory writeback failed: %s",
                type(exc).__name__,
                exc_info=True,
            )
            yield (
                "stage",
                {
                    "stage": "MemoryWriteback",
                    "status": "warning",
                    "message": "记忆回写失败（不影响回答）",
                },
            )
    else:
        MemoryAgent(settings=get_settings()).run(
            session,
            conversation.id,
            data.question,
            pipeline_result.answer_result.answer,
        )
        yield (
            "stage",
            {
                "stage": "MemoryWriteback",
                "status": "success",
                "message": "已写入会话摘要记忆",
            },
        )

    response = ChatResponse(
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
        diagnostics=diagnostics,
    )
    yield ("final", response.model_dump(mode="json"))


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
