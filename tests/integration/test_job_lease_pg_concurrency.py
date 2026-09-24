"""T063 [US2] durable-job lease is exactly-once on the production authority.

:class:`~backend.app.jobs.service.JobService` claims work with an optimistic
``version`` compare-and-set rather than ``FOR UPDATE SKIP LOCKED``: it scans the
eligible rows, then races an ``UPDATE ... WHERE id=? AND version=?`` per
candidate and moves on when the update touches zero rows. The contract suite
proves that logic on SQLite, but SQLite's coarse database-level locking cannot
exhibit the interleaving that matters -- two overlapping sessions reading the
same ``queued`` row before either writes. Only a real PostgreSQL under READ
COMMITTED can, so this test drives many concurrent workers against the live
server and asserts every job is leased exactly once, with no double transition.

It skips cleanly when no PostgreSQL is reachable (via the shared ``pg_url``
fixture). The Stage-4 tables are created here with ``metadata.create_all``
because the Alembic chain (001 expand / 002 enforce) does not yet ship a
migration for ``durable_jobs``/``outbox_events`` -- an honest gap tracked
separately; this test proves the *claim semantics* on real PG, not the
production DDL path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import DurableJob, Tenant
from backend.app.db.session import build_async_engine
from backend.app.jobs.service import JobService
from tests import conftest

TENANT_ID = "11111111-1111-1111-1111-111111111111"
JOB_COUNT = 30
WORKER_COUNT = 8


@pytest.fixture()
def lease_url(pg_url: str) -> Iterator[str]:
    """A throwaway PostgreSQL database with the Stage-4 tables created."""
    with conftest.scratch_database(pg_url, "pf_it_job_lease") as url:
        yield url


async def _prepare(url: str) -> JobService:
    engine = build_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(
            Tenant(id=TENANT_ID, code="alpha", name="Alpha", status="active")
        )
        await session.commit()
    return JobService(factory=factory)


async def _drain(service: JobService, worker_id: str) -> list[str]:
    """Lease until nothing is eligible, returning the ids this worker claimed."""
    claimed: list[str] = []
    while True:
        job = await service.lease(worker_id=worker_id, lease_seconds=60)
        if job is None:
            return claimed
        claimed.append(job.id)


async def test_concurrent_lease_claims_each_job_exactly_once(lease_url: str) -> None:
    service = await _prepare(lease_url)
    for n in range(JOB_COUNT):
        await service.enqueue(
            tenant_id=TENANT_ID,
            kind="kb_reindex",
            payload={"n": n},
            idempotency_key=f"lease-race-key-{n:04d}",
        )

    results = await asyncio.gather(
        *(_drain(service, f"worker-{w}") for w in range(WORKER_COUNT))
    )

    all_ids = [job_id for worker in results for job_id in worker]
    # Exactly-once: every queued job was claimed, and none was claimed twice.
    assert len(all_ids) == JOB_COUNT
    assert len(set(all_ids)) == JOB_COUNT

    # Every claimed job is now leased with a single attempt and a real owner.
    factory = service._factory  # noqa: SLF001 - test asserts on persisted state
    async with factory() as session:
        rows = (await session.execute(DurableJob.__table__.select())).all()
    assert len(rows) == JOB_COUNT
    for row in rows:
        assert row.state == "leased"
        assert row.attempts == 1
        assert row.lease_owner and row.lease_owner.startswith("worker-")
        # Enqueued at version 1; exactly one winning CAS bumps it to 2.
        assert row.version == 2
