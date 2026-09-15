"""T035 groundwork: the tenant-qualified memory repository.

Runs against a PostgreSQL database migrated to enforce, because that is the only
place where the guarantees under test exist: ``memory_items.tenant_id`` is NOT
NULL there and row-level security is forced, so an unscoped write does not merely
leak - it fails.

The assertions are aimed at the two ways the legacy service layer was wrong: it
filtered by owner alone, and it never stamped a tenant on insert.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.exceptions import ApplicationError
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import build_async_engine

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"
OWNER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(scope="module")
def memory_url(pg_url: str) -> Iterator[str]:
    """A migrated PostgreSQL database with the two tenants this module needs."""
    from tests import conftest

    with conftest.scratch_database(pg_url, "pf_it_memory") as url:
        conftest.alembic_upgrade(url, "001")
        conftest.run_legacy_backfill(url)
        conftest.alembic_upgrade(url, "head")
        asyncio.run(_seed(url))
        yield url


async def _seed(url: str) -> None:
    """Insert the two tenants the isolation assertions compare."""

    from backend.app.db.models import Tenant

    engine = build_async_engine(url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Tenant(id=TENANT_ALPHA, code="alpha", name="Alpha"))
            await session.flush()
            session.add(Tenant(id=TENANT_BETA, code="beta", name="Beta"))
            await session.commit()
    finally:
        await engine.dispose()


async def _store(url: str, *, tenant_id: str, content: str) -> None:
    """Write one memory item through the repository and commit it."""
    engine = build_async_engine(url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with UnitOfWork(factory=factory) as uow:
            await uow.set_tenant_context(tenant_id)
            await uow.memories.create(
                tenant_id,
                owner_type="user",
                owner_id=OWNER,
                memory_type="preference",
                content=content,
            )
            await uow.commit()
    finally:
        await engine.dispose()


async def _read(url: str, tenant_id: str) -> list[str]:
    """Return the contents one tenant can see for the shared owner id."""
    engine = build_async_engine(url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with UnitOfWork(factory=factory) as uow:
            await uow.set_tenant_context(tenant_id)
            items = await uow.memories.list_for_owner(tenant_id, "user", OWNER)
            return [item.content for item in items]
    finally:
        await engine.dispose()


def test_a_write_lands_on_the_authoritative_database(memory_url: str) -> None:
    """The insert must satisfy the enforced schema rather than omit its tenant.

    This is the sharpest evidence that the legacy path was unusable and not merely
    unsafe: the same write without a tenant is rejected by the NOT NULL column.
    """
    asyncio.run(_store(memory_url, tenant_id=TENANT_ALPHA, content="prefers bullet points"))

    assert asyncio.run(_read(memory_url, TENANT_ALPHA)) == ["prefers bullet points"]


def test_a_shared_owner_id_does_not_share_memories(memory_url: str) -> None:
    """Two tenants using the same owner id must not see each other's memories.

    The owner id is the same in both writes, so only the tenant qualification can
    keep them apart.
    """
    asyncio.run(_store(memory_url, tenant_id=TENANT_ALPHA, content="alpha only"))
    asyncio.run(_store(memory_url, tenant_id=TENANT_BETA, content="beta only"))

    assert "alpha only" in asyncio.run(_read(memory_url, TENANT_ALPHA))
    assert "beta only" not in asyncio.run(_read(memory_url, TENANT_ALPHA))
    assert "alpha only" not in asyncio.run(_read(memory_url, TENANT_BETA))


def test_a_write_without_a_tenant_is_refused(memory_url: str) -> None:
    """An unattributable write is rejected rather than stored unscoped."""

    async def attempt() -> None:
        engine = build_async_engine(memory_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with UnitOfWork(factory=factory) as uow:
                await uow.memories.create(
                    "   ",
                    owner_type="user",
                    owner_id=OWNER,
                    memory_type="preference",
                    content="should not be stored",
                )
        finally:
            await engine.dispose()

    with pytest.raises(ApplicationError):
        asyncio.run(attempt())


def test_expired_memories_are_not_returned(memory_url: str) -> None:
    """An expired item must not reach a prompt even though its row persists."""
    from datetime import UTC, datetime, timedelta

    async def store_expired() -> None:
        engine = build_async_engine(memory_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_ALPHA)
                await uow.memories.create(
                    TENANT_ALPHA,
                    owner_type="user",
                    owner_id=OWNER,
                    memory_type="conversation_fact",
                    content="expired fact",
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
                await uow.commit()
        finally:
            await engine.dispose()

    asyncio.run(store_expired())

    assert "expired fact" not in asyncio.run(_read(memory_url, TENANT_ALPHA))


def test_an_empty_owner_is_refused(memory_url: str) -> None:
    """A memory that belongs to nobody cannot be stored."""

    async def attempt() -> None:
        engine = build_async_engine(memory_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_ALPHA)
                await uow.memories.create(
                    TENANT_ALPHA,
                    owner_type="user",
                    owner_id="",
                    memory_type="preference",
                    content="orphan",
                )
        finally:
            await engine.dispose()

    with pytest.raises(ValueError):
        asyncio.run(attempt())


def test_an_async_session_is_used_end_to_end(memory_url: str) -> None:
    """The repository runs on the async session the unit of work owns."""

    async def inspect() -> type[AsyncSession]:
        engine = build_async_engine(memory_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with UnitOfWork(factory=factory) as uow:
                return type(uow.memories._session)
        finally:
            await engine.dispose()

    assert asyncio.run(inspect()) is AsyncSession


def test_a_memory_cannot_be_fetched_across_tenants(memory_url: str) -> None:
    """A memory id is only meaningful inside the tenant and owner that hold it.

    The owner is part of the lookup, so a foreign id and a missing id produce the
    same refusal rather than telling a caller that the row exists elsewhere.
    """
    from backend.app.core.exceptions import ApplicationError

    async def store_and_probe() -> tuple[str, str]:
        engine = build_async_engine(memory_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_BETA)
                item = await uow.memories.create(
                    TENANT_BETA,
                    owner_type="user",
                    owner_id=OWNER,
                    memory_type="preference",
                    content="beta secret",
                )
                await uow.commit()
                memory_id = item.id

            # Same id, different tenant: must not be readable.
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_ALPHA)
                try:
                    await uow.memories.get_for_owner(
                        TENANT_ALPHA,
                        memory_id,
                        owner_type="user",
                        owner_id=OWNER,
                    )
                    foreign = "readable"
                except ApplicationError:
                    foreign = "refused"

            # Same id and tenant, different owner: must not be readable either.
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_BETA)
                try:
                    await uow.memories.get_for_owner(
                        TENANT_BETA,
                        memory_id,
                        owner_type="user",
                        owner_id="99999999-9999-9999-9999-999999999999",
                    )
                    other_owner = "readable"
                except ApplicationError:
                    other_owner = "refused"

            # And the true owner still reads it, so the refusals are meaningful.
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_BETA)
                owned = await uow.memories.get_for_owner(
                    TENANT_BETA, memory_id, owner_type="user", owner_id=OWNER
                )
            return foreign, f"{other_owner}:{owned.content}"
        finally:
            await engine.dispose()

    foreign, own = asyncio.run(store_and_probe())

    assert foreign == "refused"
    assert own == "refused:beta secret"


def test_deleting_a_foreign_memory_is_refused(memory_url: str) -> None:
    """A delete scoped to the wrong tenant must not remove somebody else's row."""
    from backend.app.core.exceptions import ApplicationError

    async def store_then_delete_from_alpha() -> bool:
        engine = build_async_engine(memory_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_BETA)
                item = await uow.memories.create(
                    TENANT_BETA,
                    owner_type="user",
                    owner_id=OWNER,
                    memory_type="preference",
                    content="must survive",
                )
                await uow.commit()
                memory_id = item.id

            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_ALPHA)
                try:
                    await uow.memories.delete_for_owner(
                        TENANT_ALPHA, memory_id, owner_type="user", owner_id=OWNER
                    )
                    await uow.commit()
                except ApplicationError:
                    await uow.rollback()

            async with UnitOfWork(factory=factory) as uow:
                await uow.set_tenant_context(TENANT_BETA)
                survivor = await uow.memories.get_for_owner(
                    TENANT_BETA, memory_id, owner_type="user", owner_id=OWNER
                )
            return survivor.content == "must survive"
        finally:
            await engine.dispose()

    assert asyncio.run(store_then_delete_from_alpha()) is True


def test_the_legacy_memory_write_is_rejected_by_the_enforced_schema(
    memory_url: str,
) -> None:
    """Characterise the defect T035 removes, so the premise is observed not assumed.

    The legacy synchronous helper builds a ``MemoryItem`` without a tenant, and the
    enforced schema makes that column NOT NULL, so the insert is rejected outright.
    The memory layer is therefore not merely unscoped on the authoritative
    database: it cannot store anything at all.

    This test asserts the broken behaviour on purpose, because that behaviour is
    the evidence behind T035. Delete it when T035 lands: the rejection is the thing
    being fixed, so this test will fail loudly at that point rather than rot into a
    misleading comment.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import Session, create_engine

    from backend.app.services.memory_service import write_memory

    engine = create_engine(memory_url)
    try:
        with pytest.raises(IntegrityError) as rejected:
            with Session(engine) as session:
                write_memory(session, "user", OWNER, "preference", "legacy write")
    finally:
        engine.dispose()

    assert "tenant_id" in str(rejected.value)
