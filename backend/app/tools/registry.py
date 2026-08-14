"""Audited Tool registry and executor."""

import asyncio
from time import perf_counter
from typing import Any

from sqlmodel import Session, col, select

from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_request_id
from backend.app.core.redaction import redact_sensitive
from backend.app.db.models import Tool, ToolCallLog, User
from backend.app.schemas.tool import (
    ToolCallLogListResponse,
    ToolCallLogRead,
    ToolListResponse,
    ToolRead,
    ToolRunResponse,
)
from backend.app.tools.base import ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self.handlers[name] = handler

    def list(self, session: Session) -> ToolListResponse:
        tools = session.exec(select(Tool).order_by(Tool.name)).all()
        return ToolListResponse(items=[ToolRead.model_validate(tool.model_dump()) for tool in tools])

    @staticmethod
    def _to_log_read(log: ToolCallLog) -> ToolCallLogRead:
        return ToolCallLogRead(
            **log.model_dump(exclude={"input_summary", "output_summary"}),
            input_summary=redact_sensitive(log.input_summary),
            output_summary=redact_sensitive(log.output_summary),
        )

    async def execute(
        self,
        session: Session,
        name: str,
        user: User,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        agent_name: str = "manual",
    ) -> ToolRunResponse:
        tool = session.exec(select(Tool).where(Tool.name == name)).first()
        if tool is None:
            raise ApplicationError("TOOL_NOT_FOUND", "Tool not found", 404)
        if not tool.enabled:
            raise ApplicationError("TOOL_DISABLED", "Tool is disabled", 409)
        handler = self.handlers.get(name)
        if handler is None:
            raise ApplicationError("TOOL_NOT_IMPLEMENTED", "Tool handler is not implemented", 501)
        idempotency_key = str(payload.get("idempotency_key") or "")
        if idempotency_key:
            prior_logs = session.exec(
                select(ToolCallLog).where(
                    ToolCallLog.tool_name == name,
                    ToolCallLog.conversation_id == conversation_id,
                    ToolCallLog.user_id == user.id,
                )
            ).all()
            for prior in prior_logs:
                if (prior.input_summary or {}).get("idempotency_key") == idempotency_key:
                    if prior.status == "success":
                        return ToolRunResponse(
                            name=name,
                            output=prior.output_summary,
                            call_log_id=prior.id,
                            request_id=prior.request_id,
                        )
                    if prior.status == "unknown":
                        raise ApplicationError(
                            "SIDE_EFFECT_STATUS_UNKNOWN",
                            "相同操作的执行状态未知，请先核对后再继续",
                            409,
                        )
        started_at = perf_counter()
        log = ToolCallLog(
            conversation_id=conversation_id,
            agent_name=agent_name,
            tool_name=name,
            user_id=user.id,
            request_id=get_request_id(),
            input_summary=redact_sensitive(payload),
            output_summary={},
            status="success",
        )
        try:
            timeout_seconds = max(1.0, float(tool.timeout_seconds or 30))
            output = await asyncio.wait_for(
                handler(session, user, payload), timeout=timeout_seconds
            )
            log.output_summary = redact_sensitive(output)
        except TimeoutError as exc:
            log.status = "unknown"
            log.error_message = "TOOL_TIMEOUT: execution status is unknown"
            log.latency_ms = int((perf_counter() - started_at) * 1000)
            session.add(log)
            try:
                session.commit()
            except Exception as persist_exc:
                session.rollback()
                raise ApplicationError(
                    "TOOL_AUDIT_PERSIST_FAILED",
                    "Tool execution status is unknown and the audit log could not be saved",
                    500,
                ) from persist_exc
            raise ApplicationError(
                "TOOL_TIMEOUT",
                "Tool execution timed out; external side effect status is unknown",
                504,
            ) from exc
        except Exception as exc:
            log.status = "failed"
            log.error_message = (
                f"{exc.code}: {exc.message}"
                if isinstance(exc, ApplicationError)
                else f"{type(exc).__name__}: Tool execution failed"
            )
            log.latency_ms = int((perf_counter() - started_at) * 1000)
            session.add(log)
            try:
                session.commit()
            except Exception as persist_exc:
                session.rollback()
                raise ApplicationError(
                    "TOOL_AUDIT_PERSIST_FAILED", "Tool audit log could not be saved", 500
                ) from persist_exc
            raise
        log.latency_ms = int((perf_counter() - started_at) * 1000)
        session.add(log)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise ApplicationError(
                "TOOL_AUDIT_PERSIST_FAILED", "Tool audit log could not be saved", 500
            ) from exc
        return ToolRunResponse(
            name=name,
            output=output,
            call_log_id=log.id,
            request_id=log.request_id,
        )

    def logs(
        self,
        session: Session,
        page: int,
        page_size: int,
        tool_name: str | None = None,
        status: str | None = None,
    ) -> ToolCallLogListResponse:
        statement = select(ToolCallLog)
        if tool_name:
            statement = statement.where(ToolCallLog.tool_name == tool_name)
        if status:
            statement = statement.where(ToolCallLog.status == status)
        logs = session.exec(statement.order_by(col(ToolCallLog.created_at).desc())).all()
        start = (page - 1) * page_size
        return ToolCallLogListResponse(
            items=[
                self._to_log_read(log)
                for log in logs[start : start + page_size]
            ],
            total=len(logs),
            page=page,
            page_size=page_size,
        )

    def get_log(self, session: Session, log_id: str) -> ToolCallLogRead:
        log = session.get(ToolCallLog, log_id)
        if log is None:
            raise ApplicationError("TOOL_CALL_LOG_NOT_FOUND", "Tool call log not found", 404)
        return self._to_log_read(log)
