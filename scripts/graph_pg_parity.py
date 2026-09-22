"""Stage-3 storage parity: GraphService.run on SQLite vs live PostgreSQL.

Proves the shared-graph run decision is *storage-independent*: the same
``GraphService.run`` over the same deterministic dependencies yields the same
evidence-gate decision, the same terminal status, and the same ordered
``RunEvent`` sequence whether the durable AgentRun/RunEvent persistence layer is
in-memory aiosqlite or a live PostgreSQL instance.

This is the honest, reproducible core of "parity on PostgreSQL" for the
orchestration/run layer. It complements:
  - ``tests/integration/test_graph_pg_checkpoint.py`` — durable checkpoint saver
    survives a simulated process restart on live PG.
  - ``tests/integration/test_graph_entrypoint_parity.py`` — chat/stream/eval/file
    share node sequence + decision fingerprint.

It does NOT claim production-corpus answer parity: the deterministic
dependencies stand in for LightRAG/Milvus retrieval, which is not provisioned on
this box. That boundary is recorded in docs/08 §10.

Usage:
    python scripts/graph_pg_parity.py
Writes artifacts/graph/stage3/pg_parity.json and prints PARITY_OK / PARITY_FAIL.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

if sys.platform == "win32":
    # psycopg's async driver needs a selector loop on Windows.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.app.db.models import Tenant, User  # noqa: E402
from backend.app.db.repositories import UnitOfWork  # noqa: E402
from backend.app.graph.dependencies import DeterministicGraphDependencies  # noqa: E402
from backend.app.graph.service import GraphRunRequest, GraphService  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
USER_ID = "user-a"
PG_URL = os.environ.get(
    "POLICYFLOW_GRAPH_PARITY_PG_URL",
    "postgresql+psycopg://policyflow:PGPassword123%21@127.0.0.1:55432/postgres",
)


def _pg_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 55432), timeout=1.5):
            return True
    except OSError:
        return False


async def _observe(engine_url: str, *, is_sqlite: bool) -> dict:
    """Run one GraphService.run against the given engine; return the decision
    and the ordered event trace (the observable parity surface)."""
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        # Seed the tenant + user (idempotent). PostgreSQL enforces the
        # agent_runs.user_id FK to users.id that SQLite silently ignores, so the
        # run's user must exist for the durable persistence path to commit.
        async with session_factory() as session:
            if await session.get(Tenant, TENANT) is None:
                session.add(Tenant(id=TENANT, code="acme", name="Acme"))
                await session.commit()
            if await session.get(User, USER_ID) is None:
                session.add(
                    User(
                        id=USER_ID,
                        tenant_id=TENANT,
                        username="parity-user",
                        email="parity@example.com",
                        password_hash="x",
                        display_name="Parity User",
                    )
                )
                await session.commit()

        service = GraphService(
            uow_factory=lambda: UnitOfWork(factory=session_factory),
            dependencies=DeterministicGraphDependencies(tenant_id=TENANT),
        )
        result = await service.run(
            GraphRunRequest(
                entrypoint="chat",
                tenant_id=TENANT,
                user_id=USER_ID,
                run_id="",
                input_payload={
                    "message": "Can I claim approved travel expenses?",
                    "knowledge_version": "v1",
                },
            )
        )
        async with (lambda: UnitOfWork(factory=session_factory))() as uow:
            await uow.set_tenant_context(TENANT)
            run = await uow.runs.get(TENANT, result.run_id)
            events = await uow.run_events.list_for_run(TENANT, result.run_id)
        return {
            "evidence_gate": result.evidence_gate,
            "answer_startswith": result.answer[:14],
            "run_status": run.status,
            "run_kind": run.kind,
            "run_evidence_gate": run.evidence_gate,
            "event_types": [e.event_type for e in events],
            "event_sequences": [e.sequence for e in events],
            "sequences_monotonic": [e.sequence for e in events]
            == sorted(e.sequence for e in events),
        }
    finally:
        await engine.dispose()


async def _amain() -> int:
    out_dir = REPO_ROOT / "artifacts" / "graph" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)

    sqlite_obs = await _observe("sqlite+aiosqlite:///:memory:", is_sqlite=True)

    pg_obs: dict | None = None
    pg_reachable = _pg_reachable()
    if pg_reachable:
        pg_obs = await _observe(PG_URL, is_sqlite=False)

    # Parity is over the storage-independent decision surface. run_id differs by
    # construction, so it is deliberately excluded from the compared keys.
    compared_keys = [
        "evidence_gate",
        "answer_startswith",
        "run_status",
        "run_kind",
        "run_evidence_gate",
        "event_types",
        "sequences_monotonic",
    ]
    parity = None
    if pg_obs is not None:
        parity = all(sqlite_obs[k] == pg_obs[k] for k in compared_keys)

    report = {
        "pg_reachable": pg_reachable,
        "pg_url_host": "127.0.0.1:55432",
        "compared_keys": compared_keys,
        "sqlite": sqlite_obs,
        "postgres": pg_obs,
        "parity_match": parity,
        "note": (
            "Deterministic dependencies stand in for LightRAG/Milvus retrieval; "
            "this proves storage-independent run/decision parity, not "
            "production-corpus answer parity (see docs/08 §10)."
        ),
    }
    (out_dir / "pg_parity.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if not pg_reachable:
        print("PARITY_SKIP no live PostgreSQL reachable at 127.0.0.1:55432")
        print(json.dumps(sqlite_obs, indent=2))
        return 0
    if parity:
        print("PARITY_OK sqlite == postgres over decision+event surface")
        return 0
    print("PARITY_FAIL divergence between sqlite and postgres")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
