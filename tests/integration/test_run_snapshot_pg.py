"""T067 [US2] the PostgreSQL side of SSE recovery: durable run-event snapshot.

The Redis :class:`~backend.app.sse.stream.RunEventStream` is the fast replay path,
but it is bounded and TTL'd: a trimmed resume point (or a full Redis flush /
restart) leaves the client with a *gap* it cannot fill from Redis. The authority
for a run's history is PostgreSQL -- graph execution appends an append-only
:class:`~backend.app.db.models.RunEvent` milestone per stage under a monotonic
``(run_id, sequence)``. :class:`~backend.app.sse.snapshot.DurableRunSnapshot`
reads those durable milestones back, ordered by sequence, so the recovery path
can rebuild authoritative state instead of only signalling ``snapshot_required``
and offering nothing to reload from.

Proven on a real PostgreSQL because the ``(run_id, sequence)`` ordering, the
tenant scoping and the FK to ``agent_runs`` are exactly what a SQLite schema
cannot vouch for. Skips cleanly when no server is reachable (shared ``pg_url``
fixture); the Stage-4 tables are built with ``metadata.create_all`` on a
throwaway database, mirroring the T063/T066 lease and ledger tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import AgentRun, Tenant, User
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import build_async_engine
from backend.app.sse.snapshot import DurableRunSnapshot
from tests import conftest

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER_A = "cccccccc-cccc-cccc-cccc-cccccccccccc"
USER_B = "dddddddd-dddd-dddd-dddd-dddddddddddd"
RUN_A = "run-aaaa-0001"
RUN_B = "run-bbbb-0001"


async def _seed_run(session, *, tenant_id: str, user_id: str, code: str, run_id: str) -> None:
    session.add(Tenant(id=tenant_id, code=code, name=code.title(), status="active"))
    session.add(
        User(
            id=user_id,
            tenant_id=tenant_id,
            username=f"{code}-member",
            email=f"{code}@example.com",
            password_hash="not-used",
            display_name=f"{code} member",
            status="active",
        )
    )
    await session.flush()
    session.add(
        AgentRun(
            run_id=run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            kind="chat",
            thread_id=f"thr-{code}",
            status="running",
        )
    )
    await session.flush()


@pytest_asyncio.fixture
async def snapshot(pg_url: str) -> AsyncIterator[DurableRunSnapshot]:
    """A DurableRunSnapshot over a throwaway PostgreSQL with two seeded runs."""
    with conftest.scratch_database(pg_url, "pf_it_run_snapshot") as url:
        engine = build_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            await _seed_run(session, tenant_id=TENANT_A, user_id=USER_A, code="alpha", run_id=RUN_A)
            await _seed_run(session, tenant_id=TENANT_B, user_id=USER_B, code="beta", run_id=RUN_B)
            await session.commit()
        try:
            yield DurableRunSnapshot(factory=factory)
        finally:
            await engine.dispose()


async def _append(factory, *, tenant_id: str, run_id: str, events: list[tuple[int, str, dict]]) -> None:
    async with UnitOfWork(factory=factory) as uow:
        await uow.set_tenant_context(tenant_id)
        for sequence, event_type, payload in events:
            await uow.run_events.append(
                tenant_id, run_id, event_type, sequence=sequence, payload=payload,
                stage=event_type.split(".")[-1], public_status="running",
            )
        await uow.commit()


async def test_snapshot_returns_milestones_in_sequence_order(snapshot: DurableRunSnapshot) -> None:
    """The durable snapshot rebuilds a run's history ordered by ``sequence``."""
    await _append(
        snapshot._factory, tenant_id=TENANT_A, run_id=RUN_A,
        # Insert out of order to prove the snapshot orders by sequence, not insert order.
        events=[(2, "evidence.gate", {"gate": "supported"}), (1, "run.created", {"n": 1}),
                (3, "run.finalized", {"status": "succeeded"})],
    )
    events = await snapshot.milestones(tenant_id=TENANT_A, run_id=RUN_A)
    assert [e.event_type for e in events] == ["run.created", "evidence.gate", "run.finalized"]
    assert [e.id for e in events] == ["d1", "d2", "d3"]  # durable, monotonic ids
    assert [e.data["sequence"] for e in events] == [1, 2, 3]
    assert events[0].data["n"] == 1  # payload preserved verbatim


async def test_snapshot_supports_after_sequence_resume(snapshot: DurableRunSnapshot) -> None:
    """``after_sequence`` skips already-seen milestones for a partial resume."""
    await _append(
        snapshot._factory, tenant_id=TENANT_A, run_id=RUN_A,
        events=[(1, "run.created", {}), (2, "run.progress", {}), (3, "run.finalized", {})],
    )
    events = await snapshot.milestones(tenant_id=TENANT_A, run_id=RUN_A, after_sequence=1)
    assert [e.data["sequence"] for e in events] == [2, 3]


async def test_snapshot_is_tenant_scoped(snapshot: DurableRunSnapshot) -> None:
    """A tenant never sees another tenant's durable milestones."""
    await _append(snapshot._factory, tenant_id=TENANT_A, run_id=RUN_A,
                  events=[(1, "run.created", {})])
    await _append(snapshot._factory, tenant_id=TENANT_B, run_id=RUN_B,
                  events=[(1, "run.created", {})])
    # Asking for tenant A's run under tenant B's context returns nothing.
    assert await snapshot.milestones(tenant_id=TENANT_B, run_id=RUN_A) == []
    assert len(await snapshot.milestones(tenant_id=TENANT_A, run_id=RUN_A)) == 1


async def test_snapshot_survives_when_redis_holds_nothing(snapshot: DurableRunSnapshot) -> None:
    """The snapshot is pure PostgreSQL: it needs no Redis to reconstruct history.

    This is the property the gap -> snapshot fallback relies on -- after a full
    Redis flush the durable milestones are still authoritative.
    """
    await _append(
        snapshot._factory, tenant_id=TENANT_A, run_id=RUN_A,
        events=[(1, "run.created", {}), (2, "run.finalized", {})],
    )
    events = await snapshot.milestones(tenant_id=TENANT_A, run_id=RUN_A)
    assert [e.event_type for e in events] == ["run.created", "run.finalized"]


async def test_bind_produces_a_zero_arg_reader(snapshot: DurableRunSnapshot) -> None:
    """``bind`` yields the zero-arg awaitable ``sse_event_source`` calls on a gap."""
    await _append(snapshot._factory, tenant_id=TENANT_A, run_id=RUN_A,
                  events=[(1, "run.created", {})])
    reader = snapshot.bind(tenant_id=TENANT_A, run_id=RUN_A)
    events = await reader()
    assert [e.event_type for e in events] == ["run.created"]
