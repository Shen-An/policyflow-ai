"""Skill handler contracts."""

from typing import Any, Protocol


class SkillHandler(Protocol):
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]: ...
