"""Side-effect executor boundary for the graph runtime (T041).

Everything that changes the outside world (a submission, an external connector
call) goes through a side-effect executor. Keeping it behind one interface is
what lets the runtime guarantee "no side effect before an approved resume" and
"exactly once after approval" — the runtime controls precisely when
:meth:`execute` is called.

:class:`RecordingSideEffectExecutor` is the test/inspection double: it records
every call so a test can assert that an unapproved or replayed resume produced
none.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["RecordingSideEffectExecutor", "SideEffectExecutor"]


@runtime_checkable
class SideEffectExecutor(Protocol):
    async def execute(self, action: dict[str, Any]) -> dict[str, Any]: ...


class RecordingSideEffectExecutor:
    """Records each executed side effect; returns a deterministic receipt."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(action))
        return {"status": "mock", "action": dict(action), "receipt_id": f"receipt-{len(self.calls)}"}
