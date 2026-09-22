"""Durable PostgreSQL checkpoint saver survives a process restart (T044).

Proves the `langgraph-checkpoint-postgres` async saver persists a
`waiting_approval` interrupt and that a *fresh* saver + graph (a simulated
process restart) resumes from PostgreSQL and completes — the durability the
in-memory contract tests cannot show.

Skips when no test PostgreSQL is reachable. On Windows the psycopg async driver
requires a selector event loop, set at import time for this module.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langgraph.types import Command

from backend.app.graph.builder import build_graph
from backend.app.graph.dependencies import DeterministicGraphDependencies

_DEFAULT_CONN = "postgresql://policyflow:PGPassword123%21@127.0.0.1:55432/postgres"


def _conn_string() -> str:
    return os.environ.get("POLICYFLOW_GRAPH_CHECKPOINT_URL", _DEFAULT_CONN)


def _pg_reachable(conn: str) -> bool:
    # Parse host:port out of the conn string cheaply.
    try:
        hostport = conn.split("@", 1)[1].split("/", 1)[0]
        host, port = hostport.split(":")
        with socket.create_connection((host, int(port)), timeout=1.5):
            return True
    except Exception:
        return False


CONN = _conn_string()
pytestmark = pytest.mark.skipif(
    not _pg_reachable(CONN), reason="no test PostgreSQL reachable for graph checkpoint saver"
)


def _file_state(thread_seed: str) -> dict:
    return {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": f"run-{thread_seed}",
        "entrypoint": "file_workflow",
        "query": "Can I claim approved travel expenses?",
        "knowledge_version": "v1",
        "deterministic": True,
        "history": [],
        "max_tool_calls": 1,
        "tool_calls": 0,
        "requires_approval": True,
        "pending_action": {"kind": "submit_expense", "amount": 125},
        "visited": [],
    }


@pytest.mark.asyncio
async def test_pg_saver_persists_interrupt_and_resumes_after_restart() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    thread_id = f"pgthread-{os.getpid()}"
    config = {"configurable": {"thread_id": thread_id}}

    # First "process": run to the approval interrupt, then discard the saver.
    async with AsyncPostgresSaver.from_conn_string(CONN) as saver:
        await saver.setup()
        graph = build_graph(DeterministicGraphDependencies(), checkpointer=saver)
        interrupted = await graph.ainvoke(_file_state(thread_id), config)
    assert "__interrupt__" in interrupted
    assert "sandbox" in interrupted["visited"]
    assert "generate" not in interrupted["visited"]

    # Second "process": a brand-new saver + graph resumes from PostgreSQL alone.
    async with AsyncPostgresSaver.from_conn_string(CONN) as saver2:
        graph2 = build_graph(DeterministicGraphDependencies(), checkpointer=saver2)
        resumed = await graph2.ainvoke(Command(resume="approved"), config)
    assert resumed["status"] == "succeeded"
    assert resumed["approval_decision"] == "approved"
    assert "generate" in resumed["visited"]
