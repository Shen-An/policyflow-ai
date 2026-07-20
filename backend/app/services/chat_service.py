"""Chat persistence and Agent Pipeline orchestration."""

import json
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from sqlmodel import Session, col, select

from backend.app.agents.base import MemoryWorkingSet
from backend.app.agents.memory_agent import MemoryAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.plan_branch import find_option
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
    PlanOption,
    PlanStep,
    RouterResult,
    ToolCallTrace,
    TurnDiagnostics,
    UsedMemoryItem,
)
from backend.app.schemas.retrieval import LightRAGQueryMode, RetrievalRequest, RetrievalStrategy
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
        title = (question or "").strip()[:100] or "新对话"
        conversation = Conversation(user_id=user.id, title=title)
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


def _parse_iso_utc(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_awaiting_assistant_message(
    session: Session,
    conversation_id: str,
) -> Message | None:
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "assistant")
        .order_by(col(Message.created_at).desc())
    ).all()
    for message in messages:
        meta = message.meta_json or {}
        if isinstance(meta, dict) and meta.get("turn_status") == "awaiting_plan_selection":
            return message
    return None


def _load_pending_plan_context(
    session: Session,
    user: User,
    conversation: Conversation,
    *,
    selected_option_id: str | None,
    cancel: bool,
) -> tuple[Message, dict[str, Any], list[PlanOption], RouterResult, str]:
    """Load durable ToT pending state from the latest awaiting assistant stub."""
    stub = _find_awaiting_assistant_message(session, conversation.id)
    if stub is None:
        raise ApplicationError(
            "PENDING_PLAN_NOT_FOUND",
            "No pending plan selection for this conversation",
            404,
        )
    meta = stub.meta_json if isinstance(stub.meta_json, dict) else {}
    pending = meta.get("pending_plan") if isinstance(meta.get("pending_plan"), dict) else {}
    expires_at = _parse_iso_utc(pending.get("expires_at"))
    now = datetime.now(timezone.utc)
    if expires_at is not None and expires_at < now:
        raise ApplicationError(
            "PENDING_PLAN_EXPIRED",
            "Pending plan selection has expired; please ask again",
            410,
        )

    raw_options = meta.get("plan_options") or pending.get("options") or []
    options: list[PlanOption] = []
    if isinstance(raw_options, list):
        for item in raw_options:
            try:
                options.append(PlanOption.model_validate(item))
            except Exception:
                continue

    router_raw = pending.get("router_result") or meta.get("router_result") or {}
    try:
        router_result = RouterResult.model_validate(router_raw) if router_raw else RouterResult(domain="general")
    except Exception:
        router_result = RouterResult(domain="general")

    question = str(pending.get("question") or "").strip()
    if not question:
        # Fallback: previous user message.
        prev_user = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.role == "user")
            .order_by(col(Message.created_at).desc())
        ).first()
        question = (prev_user.content if prev_user else "") or ""

    if cancel:
        return stub, pending, options, router_result, question

    if not selected_option_id:
        raise ApplicationError("INVALID_PLAN_OPTION", "selected_option_id is required", 400)
    # Already resolved?
    if meta.get("turn_status") == "completed" and meta.get("selected_option_id") == selected_option_id:
        raise ApplicationError(
            "PENDING_PLAN_ALREADY_RESOLVED",
            "This plan option was already executed",
            409,
        )
    if meta.get("turn_status") not in {None, "awaiting_plan_selection"} and meta.get("selected_option_id"):
        raise ApplicationError(
            "PENDING_PLAN_ALREADY_RESOLVED",
            "Pending plan was already resolved with a different option",
            409,
        )
    if find_option(options, selected_option_id) is None:
        raise ApplicationError("INVALID_PLAN_OPTION", f"Unknown plan option: {selected_option_id}", 400)
    return stub, pending, options, router_result, question


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
        rank_raw = raw.get("rank_score")
        rank_score = float(rank_raw) if rank_raw is not None else None
        items.append(
            UsedMemoryItem(
                id=str(raw.get("id") or "") or None,
                memory_type=str(raw.get("type") or "unknown"),
                content=_clip(str(raw.get("content") or "")),
                source_slot="recalled",
                confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
                rank_score=rank_score,
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
            summary=(
                f"domain={router.domain}, task={router.task_type}, "
                f"risk={router.risk_level}, need_skill={getattr(router, 'need_skill', False)}"
            ),
            output=router.model_dump(mode="json"),
        )
    )
    # Reconstruct allowlist from router hints so non-SSE clients still see it.
    try:
        from backend.app.tools.chat_tools import resolve_allowed_tools

        allowed = sorted(
            resolve_allowed_tools(
                getattr(router, "tool_hints", None),
                need_skill=bool(getattr(router, "need_skill", False)),
            )
        )
        commands.append(
            CommandTrace(
                name="ToolAllowlist",
                status="success",
                summary="tools=" + ",".join(allowed),
                output={
                    "allowed_tools": allowed,
                    "tool_hints": list(getattr(router, "tool_hints", None) or []),
                    "need_skill": bool(getattr(router, "need_skill", False)),
                },
            )
        )
    except Exception:
        pass
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
    skills = pipeline_result.suggested_skills or []
    skill_results = getattr(pipeline_result, "skill_results", None) or []
    if skill_results:
        ok = sum(1 for item in skill_results if item.get("status") == "success")
        skill_summary = f"执行 Skill {ok}/{len(skill_results)}"
        skill_status = "success" if ok else "warning"
    elif skills:
        skill_summary = "建议 Skill：" + "、".join(item.get("name", "") for item in skills)
        skill_status = "success"
    else:
        skill_summary = "无 Skill"
        skill_status = "empty"
    commands.append(
        CommandTrace(
            name="SkillAgent",
            status=skill_status,
            summary=skill_summary,
            output={
                "suggested_skills": skills,
                "skill_results": skill_results,
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
                "skill_context_count": sum(
                    1 for item in skill_results if item.get("status") == "success"
                ),
            },
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
    # Include in-memory answer tool traces that may not yet be in ToolCallLog.
    existing_names = {(item.tool_name, item.status) for item in tools}
    for raw in getattr(pipeline_result.answer_result, "tool_trace", None) or []:
        key = (str(raw.get("tool_name") or ""), str(raw.get("status") or "success"))
        if key in existing_names:
            continue
        tools.append(
            ToolCallTrace(
                tool_name=str(raw.get("tool_name") or "unknown"),
                status=str(raw.get("status") or "success"),
                agent_name="AnswerAgent",
                input_summary=dict(raw.get("arguments") or {}),
                output_summary=dict(raw.get("output") or {})
                if isinstance(raw.get("output"), dict)
                else {"result": raw.get("output")},
                error_message=raw.get("error_message"),
                latency_ms=int(raw.get("latency_ms") or 0),
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
    tool_registry: Any | None = None,
    skill_registry: Any | None = None,
    rag_service: Any | None = None,
) -> ChatResponse:
    final: ChatResponse | None = None
    async for event_name, payload in iter_chat_events(
        session,
        user,
        data,
        pipeline,
        memory_agent=memory_agent,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        rag_service=rag_service,
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
    tool_registry: Any | None = None,
    skill_registry: Any | None = None,
    rag_service: Any | None = None,
):
    """Yield (event_name, payload) stages for SSE streaming via unified pipeline.

    Dual-request ToT HITL:
    - Request A (question): may end with status=awaiting_plan_selection + plan_options
    - Request B (selected_option_id): execute chosen plan without new user message
    """
    import asyncio

    from backend.app.tools.chat_tools import ChatToolExecutor

    settings = get_settings()
    started_at = perf_counter()
    turn_started_at = utc_now()

    is_selection = bool(data.selected_option_id) or bool(data.cancel_pending_plan)
    question = (data.question or "").strip()

    # ---------- Request B: cancel pending plan ----------
    if data.cancel_pending_plan:
        if not data.conversation_id:
            raise ApplicationError(
                "CONVERSATION_NOT_FOUND",
                "conversation_id required to cancel pending plan",
                400,
            )
        conversation = _conversation(session, user, data.conversation_id, question or "cancel")
        stub, _pending, options, router_result, _q = _load_pending_plan_context(
            session,
            user,
            conversation,
            selected_option_id=None,
            cancel=True,
        )
        meta = dict(stub.meta_json or {})
        meta["turn_status"] = "cancelled"
        meta["plan_options"] = [o.model_dump(mode="json") for o in options]
        stub.content = "已取消路径选择。如需继续，请重新提问。"
        stub.meta_json = meta
        session.add(stub)
        conversation.updated_at = utc_now()
        session.add(conversation)
        session.commit()
        session.refresh(stub)
        from backend.app.schemas.chat import ComplianceResult

        response = ChatResponse(
            conversation_id=conversation.id,
            message_id=stub.id,
            query_log_id="",
            answer=stub.content,
            citations=[],
            confidence_score=0.0,
            query_mode=data.query_mode.value,
            router_result=router_result,
            compliance=ComplianceResult(passed=True, warnings=[]),
            status="cancelled",
            reasoning_mode="tot_select",
            plan_options=options,
            awaiting_message_id=stub.id,
        )
        yield ("final", response.model_dump(mode="json"))
        return

    # ---------- Request B: select option and execute ----------
    selected_plan_steps: list[PlanStep] | None = None
    selected_router_result: RouterResult | None = None
    resume_stub: Message | None = None
    user_message: Message | None = None
    pending_snapshot: dict[str, Any] = {}

    if data.selected_option_id:
        if not data.conversation_id:
            raise ApplicationError(
                "CONVERSATION_NOT_FOUND",
                "conversation_id required for plan selection",
                400,
            )
        conversation = _conversation(session, user, data.conversation_id, question or "select")
        resume_stub, pending_snapshot, options, router_result, stored_question = (
            _load_pending_plan_context(
                session,
                user,
                conversation,
                selected_option_id=data.selected_option_id,
                cancel=False,
            )
        )
        chosen = find_option(options, data.selected_option_id)
        if chosen is None:
            raise ApplicationError("INVALID_PLAN_OPTION", "Unknown plan option", 400)
        question = stored_question or question
        if not question:
            raise ApplicationError("INVALID_PLAN_OPTION", "Pending plan has no question", 400)
        selected_plan_steps = list(chosen.steps)
        selected_router_result = router_result.model_copy(
            update={
                "plan_steps": selected_plan_steps,
                "plan_options": options,
                "plan_source": "user_selected",
                "difficulty": "branched",
                "reasoning_mode": "tot_select",
                "complexity": "multi_step",
            }
        )
        # Reuse snapshot retrieval settings when present.
        snap = pending_snapshot.get("retrieval_snapshot") or {}
        kb_ids = list(pending_snapshot.get("knowledge_base_ids") or data.knowledge_base_ids or [])
        # Find original user message (last user before stub).
        prev_users = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.role == "user")
            .order_by(col(Message.created_at).desc())
        ).all()
        user_message = prev_users[0] if prev_users else None
        yield (
            "stage",
            {
                "stage": "PlanBranch",
                "status": "success",
                "message": f"已选择路径 {chosen.id}：{chosen.title}",
            },
        )
    else:
        # ---------- Request A: new question ----------
        if not question:
            raise ApplicationError("VALIDATION_ERROR", "question is required", 400)
        conversation = _conversation(session, user, data.conversation_id, question)
        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=question,
        )
        session.add(user_message)
        session.flush()
        kb_ids = list(data.knowledge_base_ids or [])
        snap = {}

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
        working_set = await memory_agent.load(session, user, conversation, question)
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

    # Build retrieval request from request or pending snapshot.
    if is_selection and snap:
        strategy = snap.get("strategy") or data.retrieval_strategy
        if not isinstance(strategy, RetrievalStrategy):
            try:
                strategy = RetrievalStrategy(str(strategy))
            except Exception:
                strategy = data.retrieval_strategy
        query_mode = snap.get("query_mode") or data.query_mode
        if not isinstance(query_mode, LightRAGQueryMode):
            try:
                query_mode = LightRAGQueryMode(str(query_mode))
            except Exception:
                query_mode = data.query_mode
        top_k = int(snap.get("top_k") or data.top_k)
        rerank_enabled = bool(snap.get("rerank_enabled", data.rerank_enabled))
        requested_kb_ids = list(kb_ids or data.knowledge_base_ids or [])
    else:
        strategy = data.retrieval_strategy
        query_mode = data.query_mode
        top_k = data.top_k
        rerank_enabled = data.rerank_enabled
        requested_kb_ids = list(data.knowledge_base_ids or [])

    authorized_knowledge_bases = _authorized_knowledge_bases(
        session, user, requested_kb_ids
    )
    history_for_rewrite = working_set.history if working_set is not None else []
    rolling_summary = working_set.rolling_summary if working_set is not None else ""
    retrieval_query = expand_retrieval_query(
        question,
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
            strategy=strategy,
            top_k=top_k,
            rerank_enabled=rerank_enabled,
            lightrag_query_mode=query_mode,
        )
        if knowledge_bases
        else None
    )

    if retrieval_query != question.strip():
        yield (
            "stage",
            {
                "stage": "QueryRewrite",
                "status": "success",
                "message": f"已结合上下文扩写检索：{retrieval_query[:80]}",
            },
        )

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def on_stage(stage: str, status: str, message: str) -> None:
        await event_queue.put(("stage", {"stage": stage, "status": status, "message": message}))

    async def on_event(event: str, payload: dict[str, Any]) -> None:
        await event_queue.put((event, payload))

    tool_executor = None
    if tool_registry is not None:
        tool_executor = ChatToolExecutor(
            session=session,
            user=user,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            rag_service=rag_service,
            knowledge_base_ids=[kb.id for kb in knowledge_bases],
            conversation_id=conversation.id,
            retrieval_strategy=strategy,
        )

    pipeline_task = asyncio.create_task(
        pipeline.run(
            question,
            knowledge_bases,
            retrieval_request,
            enable_skill=data.enable_skill,
            working_set=working_set,
            on_stage=on_stage,
            on_event=on_event,
            session=session,
            user=user,
            tool_executor=tool_executor,
            execute_skills=bool(data.enable_skill and skill_registry is not None),
            selected_plan_steps=selected_plan_steps,
            selected_router_result=selected_router_result,
            hitl=True,
        )
    )

    pipeline_result = None
    while True:
        if pipeline_task.done() and event_queue.empty():
            pipeline_result = await pipeline_task
            break
        try:
            event_name, payload = await asyncio.wait_for(event_queue.get(), timeout=0.05)
            yield (event_name, payload)
        except asyncio.TimeoutError:
            if pipeline_task.done() and event_queue.empty():
                pipeline_result = await pipeline_task
                break
            continue

    while not event_queue.empty():
        event_name, payload = event_queue.get_nowait()
        yield (event_name, payload)

    if pipeline_result is None:
        pipeline_result = await pipeline_task

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

    turn_status = getattr(pipeline_result, "status", "completed") or "completed"
    reasoning_mode = (
        getattr(pipeline_result, "reasoning_mode", None)
        or getattr(pipeline_result.router_result, "reasoning_mode", "cot_direct")
        or "cot_direct"
    )
    plan_options = list(
        getattr(pipeline_result, "plan_options", None)
        or getattr(pipeline_result.router_result, "plan_options", None)
        or []
    )

    # ---------- Awaiting plan selection: persist stub, no query log / writeback ----------
    if turn_status == "awaiting_plan_selection":
        ttl_min = int(getattr(settings, "CHAT_TOT_PENDING_TTL_MINUTES", 60) or 60)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=max(1, ttl_min))
        pending_plan = {
            "question": question,
            "router_result": pipeline_result.router_result.model_dump(mode="json"),
            "knowledge_base_ids": [kb.id for kb in knowledge_bases],
            "retrieval_snapshot": {
                "strategy": strategy.value if hasattr(strategy, "value") else str(strategy),
                "top_k": top_k,
                "rerank_enabled": rerank_enabled,
                "query_mode": query_mode.value if hasattr(query_mode, "value") else str(query_mode),
            },
            "expires_at": expires_at.isoformat(),
            "selected_option_id": None,
            "options": [o.model_dump(mode="json") for o in plan_options],
        }
        option_lines = []
        for opt in plan_options:
            mark = "（推荐）" if opt.recommended else ""
            option_lines.append(f"- {opt.title}{mark}：{opt.summary or '见步骤清单'}")
        stub_content = (
            "本题存在多条合理执行路径，请选择一条后再继续：\n"
            + "\n".join(option_lines)
            + "\n\n（选择后将按该路径检索与回答；也可取消后重新提问。）"
        )
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=stub_content,
            meta_json=AssistantMessageMetadata(
                citations=[],
                query_log_id=None,
                confidence_score=0.0,
                query_mode=query_mode.value if hasattr(query_mode, "value") else str(query_mode),
                router_result=pipeline_result.router_result,
                suggested_skills=[],
                compliance=pipeline_result.compliance,
                diagnostics=diagnostics,
                turn_status="awaiting_plan_selection",
                reasoning_mode="tot_select",
                plan_options=plan_options,
                pending_plan=pending_plan,
            ).model_dump(mode="json"),
        )
        session.add(assistant_message)
        conversation.updated_at = utc_now()
        session.add(conversation)
        session.commit()
        session.refresh(assistant_message)

        response = ChatResponse(
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            query_log_id="",
            answer=stub_content,
            citations=[],
            confidence_score=0.0,
            query_mode=query_mode.value if hasattr(query_mode, "value") else str(query_mode),
            router_result=pipeline_result.router_result,
            compliance=pipeline_result.compliance,
            suggested_skills=[],
            diagnostics=diagnostics,
            status="awaiting_plan_selection",
            reasoning_mode="tot_select",
            plan_options=plan_options,
            awaiting_message_id=assistant_message.id,
        )
        yield ("final", response.model_dump(mode="json"))
        return

    # ---------- Completed path (normal or ToT resume) ----------
    query_mode_value = (
        query_mode.value if hasattr(query_mode, "value") else str(query_mode)
    )
    query_log = AIQueryLog(
        conversation_id=conversation.id,
        user_id=user.id,
        question=question,
        answer=pipeline_result.answer_result.answer,
        knowledge_base_ids=[knowledge_base.id for knowledge_base in knowledge_bases],
        retrieved_sources=[
            trace.model_dump(mode="json") for trace in pipeline_result.retrieval_result.trace
        ],
        confidence_score=pipeline_result.answer_result.confidence_score,
        query_mode=query_mode_value,
        latency_ms=latency_ms,
    )

    meta = AssistantMessageMetadata(
        citations=citations,
        query_log_id=query_log.id,
        confidence_score=pipeline_result.answer_result.confidence_score,
        query_mode=query_mode_value,
        router_result=pipeline_result.router_result,
        suggested_skills=pipeline_result.suggested_skills,
        compliance=pipeline_result.compliance,
        diagnostics=diagnostics,
        turn_status="completed",
        reasoning_mode=reasoning_mode,  # type: ignore[arg-type]
        plan_options=plan_options,
        selected_option_id=data.selected_option_id,
        pending_plan={},
    ).model_dump(mode="json")

    if resume_stub is not None:
        assistant_message = resume_stub
        assistant_message.content = pipeline_result.answer_result.answer
        assistant_message.meta_json = meta
        session.add(assistant_message)
    else:
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=pipeline_result.answer_result.answer,
            meta_json=meta,
        )
        session.add(assistant_message)

    conversation.updated_at = utc_now()
    session.add(query_log)
    session.add(conversation)
    session.commit()
    session.refresh(assistant_message)
    session.refresh(query_log)
    if user_message is not None:
        session.refresh(user_message)

    yield (
        "stage",
        {"stage": "MemoryWriteback", "status": "running", "message": "正在回写有价值记忆…"},
    )
    source_ids = []
    if user_message is not None:
        source_ids.append(user_message.id)
    source_ids.append(assistant_message.id)
    if memory_agent is not None:
        try:
            session.refresh(conversation)
            await memory_agent.writeback(
                session,
                user,
                conversation,
                question,
                pipeline_result.answer_result.answer,
                source_message_ids=source_ids,
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
        MemoryAgent(settings=settings).run(
            session,
            conversation.id,
            question,
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
        query_mode=query_mode_value,
        router_result=pipeline_result.router_result,
        compliance=pipeline_result.compliance,
        suggested_skills=pipeline_result.suggested_skills,
        diagnostics=diagnostics,
        status="completed",
        reasoning_mode=reasoning_mode,  # type: ignore[arg-type]
        plan_options=plan_options,
        awaiting_message_id=None,
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
