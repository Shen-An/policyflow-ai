"""Request-scoped hard limits for model/tool/retrieval work."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic

from backend.app.core.exceptions import ApplicationError

current_turn_budget: ContextVar[TurnBudget | None] = ContextVar("current_turn_budget", default=None)


@dataclass
class TurnBudget:
    max_llm_calls: int = 16
    max_retrieval_attempts: int = 5
    max_tool_calls: int = 8
    max_total_seconds: float = 180.0
    started_at: float = field(default_factory=monotonic)
    llm_calls: int = 0
    retrieval_attempts: int = 0
    tool_calls: int = 0

    def _check_time(self) -> None:
        if monotonic() - self.started_at >= self.max_total_seconds:
            raise ApplicationError("TURN_BUDGET_EXHAUSTED", "本轮处理已达到最大耗时", 504)

    def reserve(self, kind: str) -> None:
        self._check_time()
        if kind == "llm":
            current, limit = self.llm_calls, self.max_llm_calls
        elif kind == "retrieval":
            current, limit = self.retrieval_attempts, self.max_retrieval_attempts
        elif kind == "tool":
            current, limit = self.tool_calls, self.max_tool_calls
        else:
            return
        if current >= limit:
            raise ApplicationError("TURN_BUDGET_EXHAUSTED", f"本轮 {kind} 调用次数已达上限", 429)
        if kind == "llm":
            self.llm_calls += 1
        elif kind == "retrieval":
            self.retrieval_attempts += 1
        else:
            self.tool_calls += 1

    def exhausted(self, kind: str) -> bool:
        """Report whether ``kind`` has no capacity left, without consuming any.

        Lets callers degrade gracefully (return an empty, clearly-degraded
        result) instead of raising a hard failure the UI paints red.
        """

        if kind == "llm":
            return self.llm_calls >= self.max_llm_calls
        if kind == "retrieval":
            return self.retrieval_attempts >= self.max_retrieval_attempts
        if kind == "tool":
            return self.tool_calls >= self.max_tool_calls
        return False

    async def wait_for(self, awaitable):
        remaining = self.max_total_seconds - (monotonic() - self.started_at)
        if remaining <= 0:
            self._check_time()
        return await asyncio.wait_for(awaitable, timeout=remaining)

    def snapshot(self) -> dict[str, float | int]:
        return {
            "llm_calls": self.llm_calls,
            "max_llm_calls": self.max_llm_calls,
            "retrieval_attempts": self.retrieval_attempts,
            "max_retrieval_attempts": self.max_retrieval_attempts,
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "elapsed_seconds": round(monotonic() - self.started_at, 3),
            "max_total_seconds": self.max_total_seconds,
        }


def reserve_current(kind: str) -> None:
    budget = current_turn_budget.get()
    if budget is not None:
        budget.reserve(kind)


def current_budget_exhausted(kind: str) -> bool:
    budget = current_turn_budget.get()
    return budget is not None and budget.exhausted(kind)
