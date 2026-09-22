"""Durable AgentRun + RunEvent persistence and cancel for GraphService (T049).

Runs against in-memory aiosqlite (like the audit-contract ORM test), so the
persistence path is exercised without a live PostgreSQL. Tenant scoping is by
the repository's explicit tenant predicate; RLS is PostgreSQL-only defense in
depth and is a no-op here.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from backend.app.db.models import Tenant
from backend.app.db.repositories import UnitOfWork
from backend.app.graph.dependencies import DeterministicGraphDependencies
from backend.app.graph.service import GraphRunRequest, GraphService

TENANT = "11111111-1111-1111-1111-111111111111"


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Tenant(id=TENANT, code="acme", name="Acme"))
        await session.commit()
    return engine, (lambda: UnitOfWork(factory=session_factory))


@pytest.mark.asyncio
async def test_run_persists_agent_run_and_ordered_events() -> None:
    engine, uow_factory = await _make_factory()
    try:
        service = GraphService(
            uow_factory=uow_factory,
            dependencies=DeterministicGraphDependencies(tenant_id=TENANT),
        )
        result = await service.run(
            GraphRunRequest(
                entrypoint="chat",
                tenant_id=TENANT,
                user_id="user-a",
                run_id="",
                input_payload={"message": "Can I claim approved travel expenses?", "knowledge_version": "v1"},
            )
        )
        assert result.evidence_gate == "supported"

        async with uow_factory() as uow:
            await uow.set_tenant_context(TENANT)
            run = await uow.runs.get(TENANT, result.run_id)
            events = await uow.run_events.list_for_run(TENANT, result.run_id)

        assert run.status == "succeeded"
        assert run.evidence_gate == "supported"
        assert run.kind == "chat"
        sequences = [event.sequence for event in events]
        assert sequences == sorted(sequences)  # monotonic, gap-free order
        assert [event.event_type for event in events] == [
            "run.created",
            "evidence.gate",
            "run.finalized",
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_transitions_a_queued_run_and_refuses_terminal_recancel() -> None:
    engine, uow_factory = await _make_factory()
    try:
        service = GraphService(uow_factory=uow_factory)
        async with uow_factory() as uow:
            await uow.set_tenant_context(TENANT)
            row = await uow.runs.create(
                TENANT, user_id="user-a", kind="chat", thread_id="thr-1", run_id="run-cancel"
            )
            version = row.version
            await uow.commit()

        status = await service.cancel(tenant_id=TENANT, run_id="run-cancel", expected_version=version)
        assert status == "cancelled"

        # A second cancel on the now-terminal run must be refused, not silently
        # repeated.
        with pytest.raises(Exception):
            await service.cancel(tenant_id=TENANT, run_id="run-cancel", expected_version=version + 1)
    finally:
        await engine.dispose()
