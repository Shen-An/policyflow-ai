"""T078 [US1] physical deletion: order, completeness and recoverable failure.

Runs against real PostgreSQL, MinIO and Milvus; skips cleanly when they are down.
"Deleted" is the one claim that cannot be verified against a fake: a stub will
happily report an empty bucket. The whole point is that the *provider* confirms
no version and no delete marker remain.

``data-model.md`` Cross-Entity Invariant #7 -- physical deletion is complete only
when reconciliation confirms metadata, vectors and all object versions are absent
-- is what this suite holds the implementation to:

* **Order is forced.** Retrieval is withdrawn first, then vectors, then every
  object version, then the SQL rows. A reader must never be able to cite a
  material whose bytes are already half gone.
* **A plain delete is not deletion.** On a versioned bucket it only adds a delete
  marker; the bytes stay billable and recoverable. The suite deliberately leaves
  multiple versions *and* a delete marker before deleting, and requires both to
  be gone.
* **Failure parks in ``deleting``.** Never back to ``available``, and never
  "deleted" on the strength of a status column. A failed pass must be resumable.
* **Completion is attested, not asserted.** The verdict comes from
  ``confirm_physically_deleted``, which re-reads all three stores, rather than
  from the saga's own opinion of what it did.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models import (
    Material,
    MaterialVersion,
    ObjectVersion,
    VectorManifest,
)
from backend.app.storage.object_store import ObjectStoreUnavailable
from tests.stage5_env import TENANT_A, TENANT_B, stage5_environment, upload_material

POLICY_V1 = b"daily limit is 300 CNY\nreceipts above 100 CNY\n"
POLICY_V2 = b"daily limit is 500 CNY\nreceipts above 200 CNY\n"


@pytest_asyncio.fixture
async def env(pg_url: str, milvus_uri: str, object_store_config, unique_suffix):
    async with stage5_environment(
        pg_url=pg_url,
        milvus_uri=milvus_uri,
        object_store_config=object_store_config,
        scratch_name="pf_rec_physdel",
        collection_suffix=unique_suffix("physdel"),
    ) as environment:
        yield environment


async def _versions(env, material_id: str) -> list[MaterialVersion]:
    async with env.factory() as session:
        rows = await session.execute(
            select(MaterialVersion).where(MaterialVersion.material_id == material_id)
        )
        return list(rows.scalars().all())


async def _two_version_material(env) -> tuple[str, str, str]:
    """A material with two versions, so deletion has to clear more than one."""
    material_id, v1 = await upload_material(
        env, tenant_id=TENANT_A, name="Reimbursement Policy", body=POLICY_V1
    )
    _same, v2 = await upload_material(
        env,
        tenant_id=TENANT_A,
        name="Reimbursement Policy",
        body=POLICY_V2,
        material_id=material_id,
    )
    return material_id, v1, v2


# -- ordering ----------------------------------------------------------------


async def test_retrieval_is_disabled_before_anything_is_removed(env) -> None:
    """Withdrawal happens in ``request_delete``, before any store is touched."""
    material_id, v1, v2 = await _two_version_material(env)

    before = await env.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(env.knowledge_base(TENANT_A),),
        embedding_version_id=env.embedding(TENANT_A),
    )
    assert before.version_ids == (v2,), "the newest version is the served one"

    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)

    after = await env.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(env.knowledge_base(TENANT_A),),
        embedding_version_id=env.embedding(TENANT_A),
    )
    assert after.version_ids == (), "nothing may remain citable once deletion starts"

    # The bytes are deliberately still there at this point: withdrawal precedes
    # removal, so a reader loses access before the data disappears rather than
    # after.
    inventory = await env.store.inventory(
        tenant_id=TENANT_A, material_id=material_id, material_version_id=v2
    )
    assert inventory.versions, (
        "request_delete must not have removed bytes yet; order is withdraw-then-remove"
    )
    assert v1


async def test_deletion_clears_vectors_objects_and_sql_for_every_version(env) -> None:
    """The full sweep: no vectors, no object versions, no delete markers, no rows."""
    material_id, v1, v2 = await _two_version_material(env)

    # Make the object store state as awkward as it realistically gets: several
    # provider versions plus a delete marker on one of the keys. A plain delete
    # leaves the marker, so an implementation that only calls delete_object would
    # pass a naive check and fail this one.
    grant = await env.store.create_upload(
        tenant_id=TENANT_A,
        material_id=material_id,
        material_version_id=v2,
        media_type="text/plain",
        max_bytes=4096,
    )
    await env.store.put_for_test(grant, POLICY_V2 + b"\nannex A\n")
    await env.store.soft_delete_for_test(grant)
    seeded = await env.store.inventory(
        tenant_id=TENANT_A, material_id=material_id, material_version_id=v2
    )
    assert len(seeded.versions) >= 2 and seeded.delete_markers

    manifests = [
        manifest
        for version_id in (v1, v2)
        async for manifest in _manifests_for(env, version_id)
    ]

    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)
    outcome = await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    assert outcome.to_status == "deleted"

    # Vectors gone from Milvus itself, for every manifest the material ever had.
    for manifest in manifests:
        assert (
            await env.vectors.count_by_prefix(
                tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
            )
            == 0
        ), f"manifest {manifest.id} still has vectors"

    # Every object version and delete marker gone, for every material version.
    for version_id in (v1, v2):
        inventory = await env.store.inventory(
            tenant_id=TENANT_A, material_id=material_id, material_version_id=version_id
        )
        assert inventory.is_empty, (
            f"version {version_id} left {len(inventory.versions)} versions and "
            f"{len(inventory.delete_markers)} delete markers"
        )

    # SQL references gone -- physically, not behind a flag.
    async with env.factory() as session:
        assert (
            await session.execute(
                select(MaterialVersion).where(
                    MaterialVersion.material_id == material_id
                )
            )
        ).scalars().all() == []
        assert (
            await session.execute(
                select(ObjectVersion).where(ObjectVersion.material_id == material_id)
            )
        ).scalars().all() == []
        assert (
            await session.execute(
                select(VectorManifest).where(VectorManifest.material_id == material_id)
            )
        ).scalars().all() == []
        # The material row survives in ``deleted`` as the audit tombstone. It
        # carries no key, no bytes and no vector reference, and removing it would
        # orphan the material.deleted outbox event.
        material = await session.get(Material, material_id)
        assert material is not None
        assert material.status == "deleted"
        assert material.active_version_id is None


async def test_reconciliation_attests_completion(env) -> None:
    """Completion is confirmed by re-reading all three stores, not asserted."""
    material_id, _v1, _v2 = await _two_version_material(env)

    # Before deletion the check must say so, or it would attest anything.
    pending = await env.reconciler.confirm_physically_deleted(
        tenant_id=TENANT_A, material_id=material_id
    )
    assert pending, "a live material must not be reported as physically deleted"

    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)

    reasons = await env.reconciler.confirm_physically_deleted(
        tenant_id=TENANT_A, material_id=material_id
    )
    assert reasons == [], f"deletion is not actually complete: {reasons}"

    # And a sweep afterwards finds nothing: deletion must not leave orphans in its
    # own wake, which is the usual way a "complete" deletion turns out not to be.
    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert report.kinds() == set(), f"deletion left drift behind: {report.opened}"


async def test_a_status_column_alone_cannot_claim_deletion(env) -> None:
    """Flipping the status to ``deleted`` by hand must not satisfy the check.

    This is the soft-delete-pretending-to-be-done failure mode ``data-model.md``
    singles out, so it is worth an explicit test rather than trusting that nobody
    will take the shortcut later.
    """
    material_id, _v1, _v2 = await _two_version_material(env)
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None
        material.status = "deleted"
        material.version += 1
        await session.commit()

    reasons = await env.reconciler.confirm_physically_deleted(
        tenant_id=TENANT_A, material_id=material_id
    )
    assert reasons, "a status flip must not be mistaken for physical deletion"
    assert any("material_versions" in reason for reason in reasons)
    assert any("object versions" in reason for reason in reasons)


# -- recoverable failure -----------------------------------------------------


async def test_object_store_failure_keeps_deleting_and_resumes(env) -> None:
    """A failed pass stays in ``deleting`` and the next pass finishes the job."""
    material_id, v1, v2 = await _two_version_material(env)
    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)

    original = env.store.delete_all_versions
    attempts: list[str] = []

    async def failing_delete(*, tenant_id, material_id, material_version_id, **kwargs):
        attempts.append(material_version_id)
        raise ObjectStoreUnavailable("delete_objects could not reach the object store")

    env.store.delete_all_versions = failing_delete  # type: ignore[method-assign]
    try:
        outcome = await env.saga.advance_once(
            tenant_id=TENANT_A, material_id=material_id
        )
        assert outcome.to_status == "deleting"
    finally:
        env.store.delete_all_versions = original  # type: ignore[method-assign]

    assert attempts, "the deletion must really have been attempted"
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None
        assert material.status == "deleting"
        assert material.last_error_code == "OBJECT_DELETE_FAILED"
        # Metadata is still intact, so the retry knows what is left to remove.
        assert len(await _versions(env, material_id)) == 2

    # Vectors were already removed by the failed pass; the retry must tolerate
    # that rather than fail on "already gone".
    resumed = await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    assert resumed.to_status == "deleted"
    assert (
        await env.reconciler.confirm_physically_deleted(
            tenant_id=TENANT_A, material_id=material_id
        )
        == []
    )
    assert v1 and v2


async def test_a_half_deleted_material_is_never_served_again(env) -> None:
    """While stuck in ``deleting``, the material stays out of every scope."""
    material_id, _v1, _v2 = await _two_version_material(env)
    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)

    original = env.store.delete_all_versions

    async def failing_delete(**kwargs):
        raise ObjectStoreUnavailable("still unreachable")

    env.store.delete_all_versions = failing_delete  # type: ignore[method-assign]
    try:
        for _ in range(3):
            await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
            scope = await env.indexer.resolve_scope(
                tenant_id=TENANT_A,
                knowledge_base_ids=(env.knowledge_base(TENANT_A),),
                embedding_version_id=env.embedding(TENANT_A),
            )
            assert scope.version_ids == (), (
                "a material stuck in deleting must never return to the retrieval scope"
            )
    finally:
        env.store.delete_all_versions = original  # type: ignore[method-assign]


async def test_repeated_delete_passes_converge(env) -> None:
    """Deletion is idempotent, so redelivery cannot break a finished delete."""
    material_id, _v1, _v2 = await _two_version_material(env)
    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)

    for _ in range(3):
        repeat = await env.saga.advance_once(
            tenant_id=TENANT_A, material_id=material_id
        )
        assert repeat.changed is False and repeat.to_status == "deleted"
    assert (
        await env.reconciler.confirm_physically_deleted(
            tenant_id=TENANT_A, material_id=material_id
        )
        == []
    )


# -- isolation ---------------------------------------------------------------


async def test_deleting_one_tenants_material_leaves_the_other_intact(env) -> None:
    """Two tenants, same-named material: deleting one must not touch the other.

    The keys are derived per tenant, so this is really a test that the derivation
    has no collision and that the delete loop is scoped -- the failure mode would
    be silent and catastrophic.
    """
    material_a, version_a = await upload_material(
        env, tenant_id=TENANT_A, name="Reimbursement Policy", body=POLICY_V1
    )
    material_b, version_b = await upload_material(
        env, tenant_id=TENANT_B, name="Reimbursement Policy", body=POLICY_V1
    )

    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_a)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_a)

    assert (
        await env.store.inventory(
            tenant_id=TENANT_A, material_id=material_a, material_version_id=version_a
        )
    ).is_empty
    surviving = await env.store.inventory(
        tenant_id=TENANT_B, material_id=material_b, material_version_id=version_b
    )
    assert surviving.versions, "tenant B's bytes were removed by tenant A's deletion"

    scope_b = await env.indexer.resolve_scope(
        tenant_id=TENANT_B,
        knowledge_base_ids=(env.knowledge_base(TENANT_B),),
        embedding_version_id=env.embedding(TENANT_B),
    )
    assert scope_b.version_ids == (version_b,), "tenant B must still be retrievable"
    assert (
        await env.reconciler.confirm_physically_deleted(
            tenant_id=TENANT_A, material_id=material_a
        )
        == []
    )


async def test_another_tenant_cannot_request_deletion(env) -> None:
    """Deletion is the most destructive operation; ownership is re-checked."""
    from backend.app.storage.saga import SagaStateError

    material_id, _version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY_V1
    )
    with pytest.raises(SagaStateError, match="no material"):
        await env.saga.request_delete(tenant_id=TENANT_B, material_id=material_id)
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None and material.status == "available"


async def _manifests_for(env, version_id: str):
    async with env.factory() as session:
        rows = await session.execute(
            select(VectorManifest).where(
                VectorManifest.material_version_id == version_id
            )
        )
        for manifest in rows.scalars().all():
            yield manifest
