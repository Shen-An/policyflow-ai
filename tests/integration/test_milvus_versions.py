"""T074 [US1] Milvus version filtering and compare-and-set activation.

Runs against the real Milvus and PostgreSQL from ``infra/dev/compose.yaml`` and
skips cleanly when either is down. A fake vector store cannot prove any of this:
the whole point is that *Milvus itself* applies the filter before the ANN search,
and that a filter the application forgot to narrow would return another tenant's
neighbours.

The invariants under test:

* **The pre-ANN filter is mandatory and built by the module, not the caller.**
  Every search is narrowed by tenant, allowed knowledge bases, the active
  immutable version set, the embedding version and ``retrievable`` *before* the
  vector comparison runs. A caller cannot pass a raw filter string, so it cannot
  forget a term.
* **Two tenants with a same-named material never see each other's chunks**, which
  is the first half of the Independent Test.
* **An update serves the old version until the new one is switched in.** Staging
  and verifying a new version changes nothing; the compare-and-set flip is the
  single instant where the answer changes, and only one version is ever
  retrievable.
* **Milvus being down is a typed ``RETRIEVAL_UNAVAILABLE`` failure, never an
  empty result.** An empty result would be indistinguishable from "no policy
  covers this", which is how an outage turns into a confidently wrong answer.
* **A drifted ``retrievable`` projection fails closed.** The row-level flag in
  Milvus and the manifest flag in PostgreSQL must *both* say yes, so a stale
  projection can only ever shrink the result set, never leak.
* **Index and strategy metadata is read from Milvus**, not asserted by us.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel

from backend.app.db.models import (
    Department,
    EmbeddingVersion,
    KnowledgeBase,
    Material,
    MaterialVersion,
    Tenant,
    User,
)
from backend.app.db.session import build_async_engine
from backend.app.observability.errors import ErrorCode
from backend.app.retrieval.indexer import ChunkPayload, VectorIndexer
from backend.app.retrieval.milvus import (
    MANDATORY_PRE_ANN_FILTER_FIELDS,
    MilvusVectorStore,
    MilvusVectorStoreConfig,
    RetrievalScope,
    RetrievalUnavailable,
)
from tests import conftest

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DIMENSIONS = 8


def _vector(seed: float) -> list[float]:
    """A deterministic unit-ish vector; nearness is all these tests need."""
    return [seed] + [0.0] * (DIMENSIONS - 1)


@dataclass
class Fixture:
    """Everything a Milvus version test needs, already wired together."""

    indexer: VectorIndexer
    store: MilvusVectorStore
    factory: async_sessionmaker
    knowledge_base_a: str
    knowledge_base_b: str
    embedding_a: str
    embedding_b: str


async def _apply_stage5_constraints(engine) -> None:
    """Add the Stage-5 CHECKs and partial unique indexes to a create_all schema.

    The statements are rendered from migration 004's own declarations, so this
    cannot drift from production: if the migration changes a predicate, this
    picks it up. ``create_all`` builds tables from the ORM only, and the
    invariants that matter most here (at most one retrievable manifest) live in
    partial indexes the ORM does not declare.
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


@pytest_asyncio.fixture
async def milvus(pg_url: str, milvus_uri: str, unique_suffix) -> AsyncIterator[Fixture]:
    """A live Milvus collection plus a migrated-equivalent PostgreSQL schema."""
    collection = f"pf_test_{unique_suffix('chunks').replace('-', '_')}"
    config = MilvusVectorStoreConfig(
        uri=milvus_uri,
        token=None,
        tls_enabled=False,
        database="default",
        collection=collection,
        tenant_partition_key="tenant_id",
        request_timeout_seconds=30.0,
    )
    store = MilvusVectorStore(config)
    with conftest.scratch_database(pg_url, "pf_it_milvus_versions") as url:
        engine = build_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        await _apply_stage5_constraints(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        knowledge_bases: dict[str, str] = {}
        embeddings: dict[str, str] = {}
        # Committed in dependency order rather than in one flush: the tenant row
        # must exist before anything that references it, and relying on the unit
        # of work to infer that ordering across five tables is fragile.
        async with factory() as session:
            session.add(Department(id="dept-1", name="HR", code="hr"))
            for tenant_id, code in ((TENANT_A, "alpha"), (TENANT_B, "beta")):
                session.add(Tenant(id=tenant_id, code=code, name=code.title()))
            await session.commit()
        async with factory() as session:
            for tenant_id, code in ((TENANT_A, "alpha"), (TENANT_B, "beta")):
                session.add(
                    User(
                        id=f"{code}-user",
                        tenant_id=tenant_id,
                        external_subject=f"{code}-subject",
                        display_name=code,
                        username=f"{code}-user",
                        email=f"{code}@example.test",
                        password_hash="not-a-real-hash",
                    )
                )
                base = KnowledgeBase(
                    tenant_id=tenant_id,
                    code=f"{code}-hr",
                    name="HR",
                    department_id="dept-1",
                    rag_workspace=f"{code}-hr",
                )
                session.add(base)
                knowledge_bases[tenant_id] = base.id
            await session.commit()
        async with factory() as session:
            for tenant_id, code in ((TENANT_A, "alpha"), (TENANT_B, "beta")):
                embedding = EmbeddingVersion(
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_bases[tenant_id],
                    provider="deterministic",
                    model_identifier="test-embed-8d",
                    dimensions=DIMENSIONS,
                    status="active",
                )
                session.add(embedding)
                embeddings[tenant_id] = embedding.id
            await session.commit()

        await store.ensure_collection(dimensions=DIMENSIONS)
        indexer = VectorIndexer(factory=factory, vector_store=store)
        try:
            yield Fixture(
                indexer=indexer,
                store=store,
                factory=factory,
                knowledge_base_a=knowledge_bases[TENANT_A],
                knowledge_base_b=knowledge_bases[TENANT_B],
                embedding_a=embeddings[TENANT_A],
                embedding_b=embeddings[TENANT_B],
            )
        finally:
            await store.drop_collection()
            await store.close()
            await engine.dispose()


async def _seed_material(
    env: Fixture,
    *,
    tenant_id: str,
    knowledge_base_id: str,
    name: str,
    version_number: int = 1,
    source_version_id: str | None = None,
) -> tuple[str, str]:
    """Create (or extend) a material and return ``(material_id, version_id)``."""
    async with env.factory() as session:
        if source_version_id is None:
            material = Material(
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                name=name,
                source_type="policy",
                status="indexing",
            )
            session.add(material)
            await session.flush()
            material_id = material.id
        else:
            existing = await session.get(MaterialVersion, source_version_id)
            assert existing is not None
            material_id = existing.material_id
        version = MaterialVersion(
            tenant_id=tenant_id,
            material_id=material_id,
            version_number=version_number,
            source_version_id=source_version_id,
            sha256="a" * 64,
            size_bytes=10,
            media_type="text/plain",
            status="indexing",
            created_by="seed",
        )
        session.add(version)
        await session.commit()
        return material_id, version.id


# -- mandatory pre-ANN filter ------------------------------------------------


def test_mandatory_filter_fields_are_declared() -> None:
    """The required filter terms are a declared set, not an implementation detail."""
    assert MANDATORY_PRE_ANN_FILTER_FIELDS == (
        "tenant_id",
        "knowledge_base_id",
        "version_id",
        "embedding_version_id",
        "retrievable",
    )


def test_filter_expression_contains_every_mandatory_term() -> None:
    """A scope renders all five terms; a missing one would widen the search."""
    scope = RetrievalScope(
        tenant_id=TENANT_A,
        knowledge_base_ids=("kb-1", "kb-2"),
        embedding_version_id="emb-1",
        version_ids=("ver-1",),
    )
    expression = scope.filter_expression()
    for field in MANDATORY_PRE_ANN_FILTER_FIELDS:
        assert field in expression, f"the pre-ANN filter omits {field}"
    assert TENANT_A in expression
    assert "retrievable == true" in expression


def test_scope_refuses_to_be_built_without_narrowing_terms() -> None:
    """An unnarrowed scope must be impossible to construct, not merely discouraged."""
    for kwargs in (
        {"tenant_id": ""},
        {"knowledge_base_ids": ()},
        {"embedding_version_id": ""},
        {"version_ids": ()},
    ):
        base = {
            "tenant_id": TENANT_A,
            "knowledge_base_ids": ("kb-1",),
            "embedding_version_id": "emb-1",
            "version_ids": ("ver-1",),
        }
        base.update(kwargs)
        with pytest.raises(ValueError):
            RetrievalScope(**base).filter_expression()


async def test_search_does_not_accept_a_caller_supplied_filter(
    milvus: Fixture,
) -> None:
    """There is no way to hand Milvus a filter the module did not build."""
    import inspect

    parameters = set(inspect.signature(MilvusVectorStore.search).parameters)
    for forbidden in ("filter", "expr", "expression", "filters"):
        assert forbidden not in parameters, (
            f"search exposes {forbidden!r}; a caller could then omit the tenant term"
        )
    assert "scope" in parameters


# -- tenant isolation and version selection ----------------------------------


async def test_two_tenants_same_named_material_never_cross(milvus: Fixture) -> None:
    """The first half of the Independent Test: no cross-tenant results."""
    chunks = [ChunkPayload(chunk_id="c0", text="daily limit 500", vector=_vector(1.0))]
    results = {}
    for tenant_id, knowledge_base_id, embedding_id in (
        (TENANT_A, milvus.knowledge_base_a, milvus.embedding_a),
        (TENANT_B, milvus.knowledge_base_b, milvus.embedding_b),
    ):
        material_id, version_id = await _seed_material(
            milvus,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            name="Reimbursement Policy",
        )
        manifest = await milvus.indexer.stage(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            material_id=material_id,
            material_version_id=version_id,
            embedding_version_id=embedding_id,
            chunks=chunks,
        )
        await milvus.indexer.verify(manifest_id=manifest.id)
        await milvus.indexer.activate(manifest_id=manifest.id)
        results[tenant_id] = version_id

    for tenant_id, knowledge_base_id, embedding_id in (
        (TENANT_A, milvus.knowledge_base_a, milvus.embedding_a),
        (TENANT_B, milvus.knowledge_base_b, milvus.embedding_b),
    ):
        scope = await milvus.indexer.resolve_scope(
            tenant_id=tenant_id,
            knowledge_base_ids=(knowledge_base_id,),
            embedding_version_id=embedding_id,
        )
        hits = await milvus.store.search(scope=scope, query_vector=_vector(1.0), limit=10)
        assert hits, "the owning tenant must find its own chunk"
        assert {hit.tenant_id for hit in hits} == {tenant_id}
        assert {hit.version_id for hit in hits} == {results[tenant_id]}


async def test_only_the_active_version_is_retrieved_during_an_update(
    milvus: Fixture,
) -> None:
    """Staging a new version changes nothing until the CAS flip switches it in."""
    material_id, v1 = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Reimbursement Policy",
    )
    first = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=v1,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="limit 300", vector=_vector(1.0))],
    )
    await milvus.indexer.verify(manifest_id=first.id)
    await milvus.indexer.activate(manifest_id=first.id)

    async def current_texts() -> set[str]:
        scope = await milvus.indexer.resolve_scope(
            tenant_id=TENANT_A,
            knowledge_base_ids=(milvus.knowledge_base_a,),
            embedding_version_id=milvus.embedding_a,
        )
        hits = await milvus.store.search(scope=scope, query_vector=_vector(1.0), limit=10)
        return {hit.text for hit in hits}

    assert await current_texts() == {"limit 300"}

    # The updated version is staged and fully verified -- and still invisible.
    _material_id, v2 = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Reimbursement Policy",
        version_number=2,
        source_version_id=v1,
    )
    second = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=v2,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="limit 500", vector=_vector(1.0))],
    )
    await milvus.indexer.verify(manifest_id=second.id)
    assert await current_texts() == {"limit 300"}, (
        "a staged-but-unactivated version must not be retrievable; the old version "
        "keeps serving until the switch"
    )

    await milvus.indexer.activate(manifest_id=second.id)
    assert await current_texts() == {"limit 500"}, "the CAS flip is the switch"


async def test_exactly_one_retrievable_manifest_per_material(milvus: Fixture) -> None:
    """Activation demotes the previous version in the same transaction."""
    from sqlalchemy import select

    from backend.app.db.models import VectorManifest

    material_id, v1 = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Policy",
    )
    first = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=v1,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="a", vector=_vector(1.0))],
    )
    await milvus.indexer.verify(manifest_id=first.id)
    await milvus.indexer.activate(manifest_id=first.id)

    _material_id, v2 = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Policy",
        version_number=2,
        source_version_id=v1,
    )
    second = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=v2,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="b", vector=_vector(1.0))],
    )
    await milvus.indexer.verify(manifest_id=second.id)
    await milvus.indexer.activate(manifest_id=second.id)

    async with milvus.factory() as session:
        rows = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_id == material_id,
                        VectorManifest.retrievable.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].id == second.id, (
        "old and new versions must never be simultaneously authoritative"
    )


async def test_activation_rejects_an_unverified_manifest(milvus: Fixture) -> None:
    """A partially-indexed manifest must not become retrievable."""
    material_id, version_id = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Policy",
    )
    manifest = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=version_id,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="a", vector=_vector(1.0))],
    )
    # Deliberately skip verify(): indexed_count is still 0.
    with pytest.raises(ValueError, match="verif"):
        await milvus.indexer.activate(manifest_id=manifest.id)


async def test_other_embedding_version_is_filtered_out(milvus: Fixture) -> None:
    """Mixing embedding versions would mix vector spaces and destroy ranking."""
    material_id, version_id = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Policy",
    )
    manifest = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=version_id,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="a", vector=_vector(1.0))],
    )
    await milvus.indexer.verify(manifest_id=manifest.id)
    await milvus.indexer.activate(manifest_id=manifest.id)

    # A scope naming a different embedding version must find nothing, even though
    # the tenant and knowledge base match.
    scope = RetrievalScope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(milvus.knowledge_base_a,),
        embedding_version_id="some-other-embedding-version",
        version_ids=(version_id,),
    )
    assert await milvus.store.search(scope=scope, query_vector=_vector(1.0), limit=10) == []


async def test_drifted_retrievable_projection_fails_closed(milvus: Fixture) -> None:
    """Both the Milvus flag and the PostgreSQL manifest must say yes.

    PostgreSQL is the authority and the Milvus field is a projection of it. If the
    projection is stale the search can only return *fewer* rows, never another
    version's -- a drift that leaked would be a correctness hole, a drift that
    hides is a recoverable reconciliation finding.
    """
    material_id, version_id = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Policy",
    )
    manifest = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=version_id,
        embedding_version_id=milvus.embedding_a,
        chunks=[ChunkPayload(chunk_id="c0", text="a", vector=_vector(1.0))],
    )
    await milvus.indexer.verify(manifest_id=manifest.id)
    await milvus.indexer.activate(manifest_id=manifest.id)

    scope = await milvus.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(milvus.knowledge_base_a,),
        embedding_version_id=milvus.embedding_a,
    )
    assert await milvus.store.search(scope=scope, query_vector=_vector(1.0), limit=10)

    # Simulate the projection falling behind the authority.
    await milvus.store.set_retrievable(
        tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix, retrievable=False
    )
    assert (
        await milvus.store.search(scope=scope, query_vector=_vector(1.0), limit=10) == []
    ), "a stale projection must hide rows, not expose the wrong ones"


# -- unavailability ----------------------------------------------------------


async def test_milvus_unavailable_is_typed_and_fails_closed(milvus: Fixture) -> None:
    """An outage raises ``RETRIEVAL_UNAVAILABLE``; it never returns zero hits."""
    scope = RetrievalScope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(milvus.knowledge_base_a,),
        embedding_version_id=milvus.embedding_a,
        version_ids=("ver-1",),
    )
    # A port nothing listens on is an honest stand-in for "Milvus is down": the
    # client cannot connect, which is the same failure class as a crashed server.
    down = MilvusVectorStore(
        milvus.store.config.model_copy(update={"uri": "http://127.0.0.1:1"})
    )
    try:
        with pytest.raises(RetrievalUnavailable) as caught:
            await down.search(scope=scope, query_vector=_vector(1.0), limit=5)
    finally:
        await down.close()

    error = caught.value
    assert error.code == ErrorCode.RETRIEVAL_UNAVAILABLE
    assert error.status_code == 503
    assert error.retryable is True
    # The message must not leak the endpoint or credentials.
    assert "127.0.0.1" not in str(error)


async def test_unavailable_store_also_fails_closed_on_writes(milvus: Fixture) -> None:
    """Staging against a down Milvus must fail, not record a phantom manifest."""
    down = MilvusVectorStore(
        milvus.store.config.model_copy(update={"uri": "http://127.0.0.1:1"})
    )
    try:
        with pytest.raises(RetrievalUnavailable):
            await down.upsert_chunks(
                tenant_id=TENANT_A,
                knowledge_base_id=milvus.knowledge_base_a,
                subject_kind="material",
                subject_id="mat-1",
                version_id="ver-1",
                embedding_version_id=milvus.embedding_a,
                vector_id_prefix="prefix",
                chunks=[ChunkPayload(chunk_id="c0", text="a", vector=_vector(1.0))],
                retrievable=False,
            )
    finally:
        await down.close()


# -- honest metadata ---------------------------------------------------------


async def test_index_metadata_is_read_from_milvus(milvus: Fixture) -> None:
    """The reported strategy is Milvus's own, not a label we invented."""
    metadata = await milvus.store.index_metadata()
    assert metadata.collection == milvus.store.config.collection
    assert metadata.index_type, "the index type must come from describe_index"
    assert metadata.metric_type, "the metric must come from describe_index"
    assert metadata.dimensions == DIMENSIONS
    assert metadata.tenant_partition_key == "tenant_id"
    # The strategy name embeds the real index/metric so a report cannot overstate
    # what ran (for example claiming HNSW while the collection uses a flat index).
    assert metadata.index_type.lower() in metadata.strategy_name().lower()
    assert metadata.metric_type.lower() in metadata.strategy_name().lower()


async def test_collection_uses_a_tenant_partition_key(milvus: Fixture) -> None:
    """One shared collection, partitioned by tenant -- not a collection per tenant."""
    description = await milvus.store.describe()
    partition_fields = [
        field["name"]
        for field in description["fields"]
        if field.get("is_partition_key")
    ]
    assert partition_fields == ["tenant_id"], (
        f"expected tenant_id as the partition key, found {partition_fields}"
    )


async def test_deterministic_vector_ids_make_reindexing_idempotent(
    milvus: Fixture,
) -> None:
    """Re-staging the same version overwrites rather than duplicating."""
    material_id, version_id = await _seed_material(
        milvus,
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        name="Policy",
    )
    chunks = [
        ChunkPayload(chunk_id="c0", text="a", vector=_vector(1.0)),
        ChunkPayload(chunk_id="c1", text="b", vector=_vector(0.9)),
    ]
    first = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=version_id,
        embedding_version_id=milvus.embedding_a,
        chunks=chunks,
    )
    again = await milvus.indexer.stage(
        tenant_id=TENANT_A,
        knowledge_base_id=milvus.knowledge_base_a,
        material_id=material_id,
        material_version_id=version_id,
        embedding_version_id=milvus.embedding_a,
        chunks=chunks,
    )
    assert again.vector_id_prefix == first.vector_id_prefix
    assert again.id == first.id, "re-staging must reuse the manifest, not fork it"
    counted = await milvus.store.count_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=first.vector_id_prefix
    )
    assert counted == len(chunks), (
        f"deterministic ids must overwrite; found {counted} rows for {len(chunks)} chunks"
    )
