"""Shared live-infrastructure environment for the Stage-5 suites.

The saga, reconciliation and physical-deletion suites all need the same thing: a
PostgreSQL schema equivalent to what the migrations produce, a real versioned
bucket, a real Milvus collection, and the four Stage-5 components wired together.
Building that in each file would be a hundred duplicated lines and three chances
to wire it subtly differently, so it lives here.

It is a helper module rather than a ``conftest`` because it is an explicit
async context manager: the suites need to create *and tear down* infrastructure
around individual steps (for instance to simulate a crash between saga steps),
which a fixture's single setup/teardown does not express well.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import (
    Department,
    EmbeddingVersion,
    KnowledgeBase,
    Tenant,
    User,
)
from backend.app.db.session import build_async_engine
from backend.app.retrieval.indexer import ChunkPayload, VectorIndexer
from backend.app.retrieval.milvus import MilvusVectorStore, MilvusVectorStoreConfig
from backend.app.storage.object_store import ObjectStore
from backend.app.storage.reconciliation import CrossStoreReconciler
from backend.app.storage.saga import MaterialSaga
from tests import conftest

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DIMENSIONS = 8


def deterministic_chunker(payload: bytes) -> list[ChunkPayload]:
    """Split bytes into one chunk per line and embed each deterministically.

    A real embedding provider is out of scope for Stage 5 (storage lifecycle) and
    would make these tests depend on a model's availability and cost. What matters
    here is that the same bytes always produce the same chunk ids and vectors, so
    staging is genuinely idempotent and a re-index is genuinely an overwrite --
    both of which this reproduces exactly.
    """
    lines = [line for line in payload.decode("utf-8", "replace").splitlines() if line.strip()]
    chunks: list[ChunkPayload] = []
    for index, line in enumerate(lines):
        digest = hashlib.sha256(line.encode("utf-8")).digest()
        vector = [digest[position] / 255.0 for position in range(DIMENSIONS)]
        chunks.append(ChunkPayload(chunk_id=f"c{index}", text=line, vector=vector))
    return chunks


@dataclass
class Stage5Env:
    """Every Stage-5 component, wired to live infrastructure."""

    factory: async_sessionmaker
    store: ObjectStore
    vectors: MilvusVectorStore
    indexer: VectorIndexer
    saga: MaterialSaga
    reconciler: CrossStoreReconciler
    knowledge_bases: dict[str, str]
    embeddings: dict[str, str]

    def knowledge_base(self, tenant_id: str) -> str:
        return self.knowledge_bases[tenant_id]

    def embedding(self, tenant_id: str) -> str:
        return self.embeddings[tenant_id]


async def apply_stage5_constraints(engine) -> None:
    """Add migration 004's CHECKs and partial unique indexes to a create_all schema.

    Rendered from the migration's own declarations, so a predicate cannot drift
    between what production runs and what the suites assert against. ``create_all``
    builds tables from the ORM alone, and the invariants that matter most here --
    at most one retrievable manifest, the root-version rule -- live in constraints
    the ORM does not declare.
    """
    stage5 = conftest.load_stage5_migration()
    statements: list[str] = []
    for table, name, column, allowed in stage5.CHECK_CONSTRAINTS:
        values = ", ".join(f"'{value}'" for value in allowed)
        statements.append(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({column} IN ({values}))"
        )
    for name, (table, predicate) in stage5.ROW_CHECK_CONSTRAINTS.items():
        statements.append(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate})")
    for name, spec in stage5.PARTIAL_UNIQUE_INDEXES.items():
        columns = ", ".join(spec["columns"])
        statements.append(
            f"CREATE UNIQUE INDEX {name} ON {spec['table']} ({columns}) "
            f"WHERE {spec['where']}"
        )
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))


@asynccontextmanager
async def stage5_environment(
    *,
    pg_url: str,
    milvus_uri: str,
    object_store_config,
    scratch_name: str,
    collection_suffix: str,
    tenants: Sequence[str] = (TENANT_A, TENANT_B),
) -> AsyncIterator[Stage5Env]:
    """Yield a fully wired Stage-5 environment and tear it down afterwards.

    Teardown removes the Milvus collection and the scratch database. Object-store
    bytes are *not* blanket-deleted here: each suite knows which materials it
    created, and a sweeping prefix delete against a shared bucket is exactly the
    kind of blast radius Stage 5 is supposed to prevent.
    """
    collection = f"pf_test_{collection_suffix}".replace("-", "_")
    vectors = MilvusVectorStore(
        MilvusVectorStoreConfig(
            uri=milvus_uri,
            token=None,
            tls_enabled=False,
            database="default",
            collection=collection,
            tenant_partition_key="tenant_id",
            request_timeout_seconds=30.0,
        )
    )
    store = ObjectStore(object_store_config)
    await store.verify_bucket_contract()

    with conftest.scratch_database(pg_url, scratch_name) as url:
        engine = build_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        await apply_stage5_constraints(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        knowledge_bases: dict[str, str] = {}
        embeddings: dict[str, str] = {}
        # Committed in dependency order: relying on the unit of work to infer the
        # ordering across five tables is fragile, and a FK violation here looks
        # like a product bug rather than a fixture bug.
        async with factory() as session:
            session.add(Department(id="dept-1", name="HR", code="hr"))
            for index, tenant_id in enumerate(tenants):
                session.add(
                    Tenant(id=tenant_id, code=f"t{index}", name=f"Tenant {index}")
                )
            await session.commit()
        async with factory() as session:
            for index, tenant_id in enumerate(tenants):
                session.add(
                    User(
                        id=f"user-{index}",
                        tenant_id=tenant_id,
                        external_subject=f"subject-{index}",
                        display_name=f"user{index}",
                        username=f"user{index}",
                        email=f"user{index}@example.test",
                        password_hash="not-a-real-hash",
                    )
                )
                base = KnowledgeBase(
                    tenant_id=tenant_id,
                    code=f"kb-{index}",
                    name="HR",
                    department_id="dept-1",
                    rag_workspace=f"ws-{index}",
                )
                session.add(base)
                knowledge_bases[tenant_id] = base.id
            await session.commit()
        async with factory() as session:
            for tenant_id in tenants:
                embedding = EmbeddingVersion(
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_bases[tenant_id],
                    provider="deterministic",
                    model_identifier="stage5-test-8d",
                    dimensions=DIMENSIONS,
                    status="active",
                )
                session.add(embedding)
                embeddings[tenant_id] = embedding.id
            await session.commit()

        await vectors.ensure_collection(dimensions=DIMENSIONS)
        indexer = VectorIndexer(factory=factory, vector_store=vectors)
        saga = MaterialSaga(
            factory=factory,
            object_store=store,
            indexer=indexer,
            chunker=deterministic_chunker,
            retry_backoff_seconds=0.0,
        )
        reconciler = CrossStoreReconciler(
            factory=factory, object_store=store, indexer=indexer
        )
        try:
            yield Stage5Env(
                factory=factory,
                store=store,
                vectors=vectors,
                indexer=indexer,
                saga=saga,
                reconciler=reconciler,
                knowledge_bases=knowledge_bases,
                embeddings=embeddings,
            )
        finally:
            await vectors.drop_collection()
            await vectors.close()
            await store.close()
            await engine.dispose()


async def upload_material(
    env: Stage5Env,
    *,
    tenant_id: str,
    name: str,
    body: bytes,
    material_id: str | None = None,
    source_type: str = "policy",
    media_type: str = "text/plain",
) -> tuple[str, str]:
    """Run a material all the way to ``available``; return ``(material, version)``.

    Uses the real grant/PUT/verify path rather than inserting rows, so everything
    downstream is reasoning about state the production code actually produced.
    """
    draft = await env.saga.begin_upload(
        tenant_id=tenant_id,
        knowledge_base_id=env.knowledge_base(tenant_id),
        name=name,
        source_type=source_type,
        media_type=media_type,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        created_by="user-0",
        material_id=material_id,
    )
    await env.store.put_for_test(draft.grant, body)
    await env.saga.drain(tenant_id=tenant_id, material_id=draft.material_id)
    return draft.material_id, draft.material_version_id
