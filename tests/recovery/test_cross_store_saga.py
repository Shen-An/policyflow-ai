"""T076 [US1] cross-store saga recovery: partial failure, idempotent resume.

Runs against real PostgreSQL, MinIO and Milvus; skips cleanly when they are down.
Recovery is the whole reason the saga exists, and it can only be demonstrated
against stores that really can be left half-written.

What is proven here:

* a happy-path upload walks ``pending_upload -> scanning -> indexing -> available``
  and emits exactly one outbox event per transition;
* failing at *each* step leaves the material retryable, and re-running converges to
  the same final state as if it had never failed -- that is the idempotence claim;
* re-running a step whose work is already durably present changes nothing, so a
  redelivered outbox message cannot double-apply;
* an update keeps serving the old version until activation, and afterwards there
  is exactly one retrievable manifest -- the dual-write window is transient, never
  a steady state;
* delete failures keep the material in ``deleting`` and never fall back to
  ``available``, because re-advertising half-deleted content is worse than staying
  stuck;
* the attempt budget escalates to ``error``, and ``recover`` resumes at the step
  the *stores* imply rather than at a remembered cursor.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models import (
    Material,
    MaterialVersion,
    ObjectVersion,
    OutboxEvent,
    VectorManifest,
)
from backend.app.storage.object_store import ObjectStoreUnavailable
from backend.app.storage.saga import AGGREGATE_TYPE, SagaStateError
from tests.stage5_env import TENANT_A, stage5_environment, upload_material

POLICY_V1 = b"daily reimbursement limit is 300 CNY\nreceipts required above 100 CNY\n"
POLICY_V2 = b"daily reimbursement limit is 500 CNY\nreceipts required above 200 CNY\n"


@pytest_asyncio.fixture
async def env(pg_url: str, milvus_uri: str, object_store_config, unique_suffix):
    async with stage5_environment(
        pg_url=pg_url,
        milvus_uri=milvus_uri,
        object_store_config=object_store_config,
        scratch_name="pf_rec_saga",
        collection_suffix=unique_suffix("saga"),
    ) as environment:
        yield environment


async def _status(env, material_id: str) -> str:
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None
        return material.status


async def _events(env, material_id: str) -> list[str]:
    async with env.factory() as session:
        rows = await session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == AGGREGATE_TYPE,
                OutboxEvent.aggregate_id == material_id,
            )
            .order_by(OutboxEvent.aggregate_version)
        )
        return [row.event_type for row in rows.scalars().all()]


async def _begin(env, *, body: bytes = POLICY_V1, material_id: str | None = None):
    draft = await env.saga.begin_upload(
        tenant_id=TENANT_A,
        knowledge_base_id=env.knowledge_base(TENANT_A),
        name="Reimbursement Policy",
        source_type="policy",
        media_type="text/plain",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        created_by="user-0",
        material_id=material_id,
    )
    return draft


# -- happy path --------------------------------------------------------------


async def test_upload_walks_the_full_saga_and_emits_one_event_per_step(env) -> None:
    """Every transition is durable and published exactly once."""
    draft = await _begin(env)
    assert await _status(env, draft.material_id) == "pending_upload"

    await env.store.put_for_test(draft.grant, POLICY_V1)
    outcomes = await env.saga.drain(tenant_id=TENANT_A, material_id=draft.material_id)

    assert [(step.from_status, step.to_status) for step in outcomes if step.changed] == [
        ("pending_upload", "scanning"),
        ("scanning", "indexing"),
        ("indexing", "available"),
    ]
    assert await _status(env, draft.material_id) == "available"
    assert await _events(env, draft.material_id) == [
        "material.upload_requested",
        "material.upload_verified",
        "material.scan_passed",
        "material.available",
    ]

    async with env.factory() as session:
        material = await session.get(Material, draft.material_id)
        version = await session.get(MaterialVersion, draft.material_version_id)
        assert material is not None and version is not None
        assert material.active_version_id == version.id
        assert version.status == "available"
        assert version.object_version_id is not None
        # The retry state is cleared on success, so the next step starts fresh.
        assert material.attempts == 0 and material.last_error_code is None


async def test_advance_is_a_no_op_once_terminal(env) -> None:
    """A redelivered nudge for a finished material must not change anything."""
    draft = await _begin(env)
    await env.store.put_for_test(draft.grant, POLICY_V1)
    await env.saga.drain(tenant_id=TENANT_A, material_id=draft.material_id)

    before = await _events(env, draft.material_id)
    repeat = await env.saga.advance_once(
        tenant_id=TENANT_A, material_id=draft.material_id
    )
    assert repeat.changed is False and repeat.to_status == "available"
    assert await _events(env, draft.material_id) == before, (
        "a no-op step must not publish an event"
    )


# -- partial failure at each step --------------------------------------------


async def test_upload_step_without_bytes_stays_retryable_then_converges(env) -> None:
    """Failing verification parks the material; uploading later converges."""
    draft = await _begin(env)
    # The client never completed the PUT.
    first = await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)
    assert first.changed is False
    assert await _status(env, draft.material_id) == "pending_upload"

    async with env.factory() as session:
        material = await session.get(Material, draft.material_id)
        assert material is not None
        assert material.attempts == 1
        assert material.last_error_code == "UPLOAD_NOT_VERIFIED"
        assert material.next_attempt_at is not None, (
            "a recoverable step must record when to try again"
        )

    # No object row was created for a failed verification.
    async with env.factory() as session:
        rows = await session.execute(
            select(ObjectVersion).where(ObjectVersion.material_id == draft.material_id)
        )
        assert rows.scalars().all() == []

    await env.store.put_for_test(draft.grant, POLICY_V1)
    await env.saga.drain(tenant_id=TENANT_A, material_id=draft.material_id)
    assert await _status(env, draft.material_id) == "available"


async def test_index_step_failure_leaves_nothing_retrievable(env) -> None:
    """A material that failed to index must not be citable.

    The embedding version is retired mid-saga, which is a realistic operational
    mistake: it makes the index step fail after the bytes are already stored.
    """
    from backend.app.db.models import EmbeddingVersion

    draft = await _begin(env)
    await env.store.put_for_test(draft.grant, POLICY_V1)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)
    assert await _status(env, draft.material_id) == "indexing"

    async with env.factory() as session:
        embedding = await session.get(EmbeddingVersion, env.embedding(TENANT_A))
        assert embedding is not None
        embedding.status = "retired"
        await session.commit()

    outcome = await env.saga.advance_once(
        tenant_id=TENANT_A, material_id=draft.material_id
    )
    assert outcome.changed is False
    assert await _status(env, draft.material_id) == "indexing"
    async with env.factory() as session:
        material = await session.get(Material, draft.material_id)
        assert material is not None
        assert material.last_error_code == "NO_ACTIVE_EMBEDDING_VERSION"
        assert material.active_version_id is None, (
            "a material that never finished indexing must not be pointed at"
        )
        rows = await session.execute(
            select(VectorManifest).where(
                VectorManifest.material_id == draft.material_id,
                VectorManifest.retrievable.is_(True),
            )
        )
        assert rows.scalars().all() == []

    # Restoring the embedding version lets the same step converge.
    async with env.factory() as session:
        embedding = await session.get(EmbeddingVersion, env.embedding(TENANT_A))
        assert embedding is not None
        embedding.status = "active"
        await session.commit()
    await env.saga.drain(tenant_id=TENANT_A, material_id=draft.material_id)
    assert await _status(env, draft.material_id) == "available"


async def test_exhausting_the_budget_escalates_to_error_then_recovers(env) -> None:
    """The attempt budget terminates, and recovery resumes from durable state."""
    draft = await _begin(env)
    async with env.factory() as session:
        material = await session.get(Material, draft.material_id)
        assert material is not None
        material.max_attempts = 2
        await session.commit()

    await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)
    escalated = await env.saga.advance_once(
        tenant_id=TENANT_A, material_id=draft.material_id
    )
    assert escalated.to_status == "error" and escalated.changed is True
    assert "material.failed" in await _events(env, draft.material_id)

    # An errored material is not advanced blindly: recovery is explicit.
    stuck = await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)
    assert stuck.changed is False and stuck.to_status == "error"

    await env.store.put_for_test(draft.grant, POLICY_V1)
    recovered = await env.saga.recover(tenant_id=TENANT_A, material_id=draft.material_id)
    # No object row yet, so the stores imply the upload step -- not "indexing"
    # just because the saga happened to get that far before.
    assert recovered.to_status == "pending_upload"
    async with env.factory() as session:
        material = await session.get(Material, draft.material_id)
        assert material is not None
        assert material.attempts == 0, "recovery must reset the exhausted budget"

    await env.saga.drain(tenant_id=TENANT_A, material_id=draft.material_id)
    assert await _status(env, draft.material_id) == "available"


async def test_recover_resumes_at_indexing_when_the_object_already_exists(env) -> None:
    """Recovery reads the stores, so it does not redo work that is already done."""
    draft = await _begin(env)
    await env.store.put_for_test(draft.grant, POLICY_V1)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)

    async with env.factory() as session:
        material = await session.get(Material, draft.material_id)
        assert material is not None
        material.status = "error"
        material.version += 1
        await session.commit()

    recovered = await env.saga.recover(tenant_id=TENANT_A, material_id=draft.material_id)
    assert recovered.to_status == "indexing", (
        "the object exists, so the upload step must not run again"
    )


async def test_recovering_a_quarantined_version_is_refused(env) -> None:
    """An infected version is never retried; it needs a new version."""
    draft = await _begin(env)
    await env.store.put_for_test(draft.grant, POLICY_V1)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=draft.material_id)

    async with env.factory() as session:
        version = await session.get(MaterialVersion, draft.material_version_id)
        assert version is not None and version.object_version_id is not None
        object_version = await session.get(ObjectVersion, version.object_version_id)
        assert object_version is not None
        object_version.scan_status = "infected"
        object_version.version += 1
        await session.commit()

    outcome = await env.saga.advance_once(
        tenant_id=TENANT_A, material_id=draft.material_id
    )
    assert outcome.to_status == "error"
    assert "material.quarantined" in await _events(env, draft.material_id)
    async with env.factory() as session:
        version = await session.get(MaterialVersion, draft.material_version_id)
        assert version is not None and version.status == "quarantined"

    with pytest.raises(SagaStateError, match="quarantined"):
        await env.saga.recover(tenant_id=TENANT_A, material_id=draft.material_id)


# -- no permanent dual write -------------------------------------------------


async def test_update_overlap_is_transient_not_a_steady_state(env) -> None:
    """After an update exactly one version is served and one manifest is live."""
    material_id, v1 = await upload_material(
        env, tenant_id=TENANT_A, name="Reimbursement Policy", body=POLICY_V1
    )

    draft = await _begin(env, body=POLICY_V2, material_id=material_id)
    assert draft.version_number == 2
    await env.store.put_for_test(draft.grant, POLICY_V2)

    # Before activation the previous version is still the served one.
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None and material.active_version_id == v1

    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)

    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None
        assert material.active_version_id == draft.material_version_id
        old = await session.get(MaterialVersion, v1)
        assert old is not None and old.status == "superseded"

        live = (
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
        assert len(live) == 1 and live[0].material_version_id == draft.material_version_id

        # The superseded version's manifest is not merely unflagged, it is deleted:
        # leaving it would be a second set of vectors for the same material that
        # nothing owns, which is an orphan by any later sweep's definition.
        retired = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_version_id == v1
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(manifest.deletion_state == "deleted" for manifest in retired)

    # And the vectors really are gone from Milvus, not just marked in SQL.
    for manifest in retired:
        assert (
            await env.vectors.count_by_prefix(
                tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
            )
            == 0
        )


async def test_rerunning_the_index_step_does_not_duplicate_vectors(env) -> None:
    """Deterministic ids make a redelivered index step an overwrite."""
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY_V1
    )
    async with env.factory() as session:
        manifest = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_version_id == version_id
                    )
                )
            )
            .scalars()
            .one()
        )
    before = await env.vectors.count_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
    )

    # Force the saga back to ``indexing`` as a crash-then-redelivery would.
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None
        material.status = "indexing"
        material.version += 1
        await session.commit()
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)

    after = await env.vectors.count_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
    )
    assert after == before, f"re-indexing duplicated vectors ({before} -> {after})"
    assert await _status(env, material_id) == "available"


# -- deletion ----------------------------------------------------------------


async def test_delete_request_stops_retrieval_immediately(env) -> None:
    """Entering ``deleting`` withdraws the material in the same call."""
    material_id, _version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY_V1
    )
    outcome = await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)
    assert outcome.to_status == "deleting"

    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None
        assert material.active_version_id is None
        live = (
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
        assert live == [], "a doomed material must stop being citable at once"

    scope = await env.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(env.knowledge_base(TENANT_A),),
        embedding_version_id=env.embedding(TENANT_A),
    )
    assert scope.version_ids == (), "no version remains in the retrieval scope"


async def test_delete_failure_stays_in_deleting_and_never_reverts(env) -> None:
    """A failed deletion is resumable; it must not re-advertise the material."""
    material_id, _version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY_V1
    )
    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)

    # The object store becomes unreachable partway through deletion.
    original = env.store.delete_all_versions
    calls: list[int] = []

    async def failing_delete(**kwargs):
        calls.append(1)
        raise ObjectStoreUnavailable("delete_objects could not reach the object store")

    env.store.delete_all_versions = failing_delete  # type: ignore[method-assign]
    try:
        # One more pass than the attempt budget, to prove ``deleting`` is excluded
        # from escalation rather than merely not having reached it yet.
        for _ in range(6):
            outcome = await env.saga.advance_once(
                tenant_id=TENANT_A, material_id=material_id
            )
            assert outcome.to_status == "deleting", (
                "a failed deletion must never fall back to available"
            )
        async with env.factory() as session:
            material = await session.get(Material, material_id)
            assert material is not None
            assert material.status == "deleting"
            assert material.last_error_code == "OBJECT_DELETE_FAILED"
            # Past the budget, ``deleting`` is excluded from escalation on purpose:
            # a half-deleted material must keep retrying deletion rather than move
            # to a state that looks resumable as an upload.
            assert material.attempts > material.max_attempts
    finally:
        env.store.delete_all_versions = original  # type: ignore[method-assign]

    assert calls, "the failing delete must actually have been attempted"
    final = await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    assert final.to_status == "deleted"
    assert await _status(env, material_id) == "deleted"


async def test_delete_is_idempotent_across_redelivery(env) -> None:
    """Re-running deletion after it completed is a no-op, not an error."""
    material_id, _version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY_V1
    )
    await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    events = await _events(env, material_id)

    repeat = await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    assert repeat.changed is False and repeat.to_status == "deleted"
    assert await _events(env, material_id) == events, (
        "a redelivered delete must not publish material.deleted twice"
    )


# -- tenant isolation --------------------------------------------------------


async def test_another_tenant_cannot_drive_the_saga(env) -> None:
    """A material is addressable only by its owner, and absence is indistinguishable."""
    from tests.stage5_env import TENANT_B

    material_id, _version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY_V1
    )
    with pytest.raises(SagaStateError, match="no material"):
        await env.saga.advance_once(tenant_id=TENANT_B, material_id=material_id)
    with pytest.raises(SagaStateError, match="no material"):
        await env.saga.request_delete(tenant_id=TENANT_B, material_id=material_id)
    # The same message for a genuinely absent id: a different one would confirm
    # that another tenant's material exists.
    with pytest.raises(SagaStateError, match="no material"):
        await env.saga.advance_once(tenant_id=TENANT_B, material_id="does-not-exist")


# -- Independent Test (combined) ---------------------------------------------


async def test_independent_test_two_tenants_same_name_during_update(env) -> None:
    """The Phase-5 Independent Test as one scenario, not as separate halves.

    Two tenants each own a material named "Reimbursement Policy". Tenant A updates
    to v2 while tenant B keeps v1. At every observable point -- before the update,
    while v2 is staged but not activated, and after the CAS switch -- each tenant
    must retrieve exactly its own current active immutable version and never the
    other tenant's or a superseded one.
    """
    from tests.stage5_env import TENANT_B

    a_material, a_v1 = await upload_material(
        env, tenant_id=TENANT_A, name="Reimbursement Policy", body=POLICY_V1
    )
    _b_material, b_v1 = await upload_material(
        env, tenant_id=TENANT_B, name="Reimbursement Policy", body=POLICY_V1
    )

    async def served(tenant_id: str) -> tuple[set[str], set[str]]:
        scope = await env.indexer.resolve_scope(
            tenant_id=tenant_id,
            knowledge_base_ids=(env.knowledge_base(tenant_id),),
            embedding_version_id=env.embedding(tenant_id),
        )
        probe = env.saga._chunker(POLICY_V1)[0].vector  # noqa: SLF001
        hits = await env.vectors.search(scope=scope, query_vector=list(probe), limit=20)
        return {hit.version_id for hit in hits}, {hit.tenant_id for hit in hits}

    assert await served(TENANT_A) == ({a_v1}, {TENANT_A})
    assert await served(TENANT_B) == ({b_v1}, {TENANT_B})

    # Tenant A stages v2 up to (but not through) activation.
    draft = await _begin(env, body=POLICY_V2, material_id=a_material)
    await env.store.put_for_test(draft.grant, POLICY_V2)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=a_material)
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=a_material)
    assert await _status(env, a_material) == "indexing"
    assert await served(TENANT_A) == ({a_v1}, {TENANT_A}), "v2 must stay invisible"
    assert await served(TENANT_B) == ({b_v1}, {TENANT_B})

    # The CAS switch.
    await env.saga.advance_once(tenant_id=TENANT_A, material_id=a_material)
    assert await served(TENANT_A) == ({draft.material_version_id}, {TENANT_A})
    assert await served(TENANT_B) == ({b_v1}, {TENANT_B}), (
        "tenant A's update must not disturb tenant B's same-named material"
    )
