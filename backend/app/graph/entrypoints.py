"""Entrypoint identity and request shape for the shared graph (T039).

The point of a single graph is that these entrypoints are *labels*, not
separate pipelines: chat, stream, eval and the file workflow all enter the same
node sequence. The label only selects the required permission scope and whether
side effects are allowed (eval runs deterministic and side-effect free).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.app.auth.principal import RequestPrincipal

__all__ = [
    "ENTRYPOINT_REQUIRED_SCOPE",
    "GraphEntrypoint",
    "GraphRequest",
]


class GraphEntrypoint(str, Enum):
    CHAT = "chat"
    STREAM = "stream"
    EVAL = "eval"
    FILE_WORKFLOW = "file_workflow"


# Least-privilege scope each entrypoint requires. ``graph:*`` is the wildcard
# that satisfies any of these.
ENTRYPOINT_REQUIRED_SCOPE: dict[GraphEntrypoint, str] = {
    GraphEntrypoint.CHAT: "graph:invoke",
    GraphEntrypoint.STREAM: "graph:stream",
    GraphEntrypoint.EVAL: "graph:eval",
    GraphEntrypoint.FILE_WORKFLOW: "graph:file_workflow",
}


@dataclass(frozen=True)
class GraphRequest:
    entrypoint: GraphEntrypoint
    principal: RequestPrincipal
    run_id: str
    input_payload: dict[str, Any] = field(default_factory=dict)
    decision_seed: str | None = None
