"""Fixtures for Phase 4 recovery tests.

Recovery is about durability across process boundaries, so the fixture hands out
a SQLite *file* (not in-memory) and a helper that builds a brand-new engine +
``JobService`` against that same file. That models a restarted API/worker
instance: the service object holds no state, and correctness must come from the
database. PostgreSQL is the production authority; these tests prove the *logic*
on the always-available SQLite path and skip nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import Tenant
from backend.app.db.session import build_async_engine
from backend.app.jobs.service import JobService

TENANT = "11111111-1111-1111-1111-111111111111"


@dataclass
class JobEnv:
    """A durable SQLite database plus a factory for fresh service instances."""

    url: str

    def fresh_service(self, **kwargs) -> JobService:
        """Build a JobService on a new engine over the same file (a 'restart')."""
        engine = build_async_engine(self.url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return JobService(factory=factory, **kwargs)


@pytest_asyncio.fixture
async def jobs(tmp_path) -> AsyncIterator[JobEnv]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}"
    engine = build_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT, code="acme", name="Acme"))
        await session.commit()
    await engine.dispose()  # release; each service opens its own engine
    yield JobEnv(url=url)
