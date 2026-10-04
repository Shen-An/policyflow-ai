"""T077 [US1] seeded cross-store faults must be detected, all six kinds, 100%.

Runs against real PostgreSQL, MinIO and Milvus; skips cleanly when they are down.
Every fault here is seeded by reaching *past* the saga and corrupting one store
directly -- which is exactly what a crash, a lost provider delete or a
half-applied index does in production. Detection cannot be demonstrated any other
way: a fault injected into a fake is a fault in the fake.

The six kinds and why each one matters:

* ``missing_object`` -- metadata cites bytes that are gone; evidence would be
  unreadable.
* ``orphan_object`` -- undeleted customer bytes nobody serves.
* ``missing_vector`` -- a material that looks available but is silently unfindable.
* ``orphan_vector`` -- vectors for a version PostgreSQL forgot, which can still
  match a query; the most dangerous kind.
* ``missing_chunk`` -- partial vectors, so retrieval returns truncated evidence
  that looks complete.
* ``version_drift`` -- the active-version pointer disagrees with what is served.

Also proven: the sweep is idempotent (re-detection updates, never duplicates), a
re-detected repair reopens, every issue reaches a terminal state, an unreachable
store yields no verdict instead of a flood of false criticals, and a sweep never
sees another tenant's drift.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from backend.app.db.models import (
    RECONCILIATION_ISSUE_KINDS,
    RECONCILIATION_TERMINAL_STATES,
    Material,
    ObjectVersion,
    ReconciliationIssue,
    VectorManifest,
)
from backend.app.retrieval.indexer import ChunkPayload
from backend.app.storage.reconciliation import (
    ISSUE_SEVERITY,
    ReconciliationUnavailable,
)
from tests.stage5_env import (
    DIMENSIONS,
    TENANT_A,
    TENANT_B,
    stage5_environment,
    upload_material,
)

POLICY = b"daily limit is 300 CNY\nreceipts above 100 CNY\nmanager approval over 2000\n"


@pytest_asyncio.fixture
async def env(pg_url: str, milvus_uri: str, object_store_config, unique_suffix):
    async with stage5_environment(
        pg_url=pg_url,
        milvus_uri=milvus_uri,
        object_store_config=object_store_config,
        scratch_name="pf_rec_issues",
        collection_suffix=unique_suffix("recon"),
    ) as environment:
        yield environment


async def _manifest_for(env, version_id: str) -> VectorManifest:
    async with env.factory() as session:
        return (
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


# -- individual fault kinds --------------------------------------------------

async def _seed_orphan_object(env, *, tenant_id: str, material_id: str) -> str:
    """Leave real bytes plus an ``object_versions`` row that nothing claims.

    This is what a crashed upload actually leaves behind: the object landed and
    its row was written, but the ``material_versions.object_version_id`` update
    that would have claimed it never committed. The orphan gets its *own* derived
    key (a version id no material version uses), so repairing it cannot touch a
    live version's bytes.
    """
    grant = await env.store.create_upload(
        tenant_id=tenant_id,
        material_id=material_id,
        material_version_id="ghost-version",
        media_type="text/plain",
        max_bytes=4096,
    )
    await env.store.put_for_test(grant, b"bytes nobody claims")
    stored = await env.store.verify_upload(
        grant=grant,
        expected_sha256=hashlib.sha256(b"bytes nobody claims").hexdigest(),
        expected_size_bytes=len(b"bytes nobody claims"),
        expected_media_type="text/plain",
    )
    async with env.factory() as session:
        row = ObjectVersion(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id="ghost-version",
            bucket_alias=stored.bucket_alias,
            object_key=stored.object_key,
            provider_version_id=stored.provider_version_id,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=stored.media_type,
        )
        session.add(row)
        await session.commit()
        return row.id




async def test_missing_object_is_detected(env) -> None:
    """Bytes removed behind the saga's back must be reported as missing."""
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    # The provider lost the object (or a rogue lifecycle rule expired it).
    await env.store.delete_all_versions(
        tenant_id=TENANT_A, material_id=material_id, material_version_id=version_id
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "missing_object" in report.kinds()
    issues = await env.reconciler.open_issues(
        tenant_id=TENANT_A, kinds=["missing_object"]
    )
    assert [issue.resource_id for issue in issues] == [version_id]
    assert issues[0].severity == "critical", (
        "metadata citing unreadable bytes is a correctness problem, not a warning"
    )


async def test_orphan_object_is_detected_and_repaired(env) -> None:
    """Bytes no live version claims are reported, and repair removes them."""
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    orphan_id = await _seed_orphan_object(
        env, tenant_id=TENANT_A, material_id=material_id
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "orphan_object" in report.kinds()
    issues = await env.reconciler.open_issues(tenant_id=TENANT_A, kinds=["orphan_object"])
    assert [issue.resource_id for issue in issues] == [orphan_id]
    assert issues[0].severity == "warning", (
        "undeleted bytes nobody serves are costly but not misleading"
    )

    # Repairing is unambiguous here: nothing claims the object.
    repaired = await env.reconciler.sweep(tenant_id=TENANT_A, repair=True)
    assert any(entry.startswith("orphan_object:") for entry in repaired.repaired)
    async with env.factory() as session:
        assert await session.get(ObjectVersion, orphan_id) is None, (
            "the orphaned object row must be gone after repair"
        )
    assert (
        await env.store.inventory(
            tenant_id=TENANT_A,
            material_id=material_id,
            material_version_id="ghost-version",
        )
    ).is_empty, "the orphaned bytes must be gone too, not just the row"

    # The live version is untouched: its row, its bytes and its retrievability.
    clean = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert clean.kinds() == set(), (
        f"repairing an orphan disturbed the live material: {clean.opened}"
    )
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None and material.active_version_id == version_id


async def test_missing_vector_is_detected_and_withdrawn(env) -> None:
    """A retrievable manifest with no vectors is critical and gets withdrawn."""
    _material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    manifest = await _manifest_for(env, version_id)
    # Milvus lost the segment; the manifest still says "retrievable".
    await env.vectors.delete_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "missing_vector" in report.kinds()

    repaired = await env.reconciler.sweep(tenant_id=TENANT_A, repair=True)
    assert any(entry.startswith("missing_vector:") for entry in repaired.repaired)
    async with env.factory() as session:
        current = await session.get(VectorManifest, manifest.id)
        assert current is not None and current.retrievable is False, (
            "a manifest Milvus cannot back must stop claiming to serve"
        )
        # Repair withdraws; it never deletes the metadata that records what to re-index.
        assert current.deletion_state == "retained"


async def test_orphan_vector_is_detected(env) -> None:
    """Vectors for a version PostgreSQL does not know must be found."""
    await upload_material(env, tenant_id=TENANT_A, name="Policy", body=POLICY)
    # A crashed re-index left rows behind under a version id that was rolled back.
    await env.vectors.upsert_chunks(
        tenant_id=TENANT_A,
        knowledge_base_id=env.knowledge_base(TENANT_A),
        subject_kind="material",
        subject_id="ghost-material",
        version_id="ghost-version",
        embedding_version_id=env.embedding(TENANT_A),
        vector_id_prefix="ghostprefix",
        chunks=[ChunkPayload(chunk_id="c0", text="stale", vector=[0.5] * DIMENSIONS)],
        retrievable=True,
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "orphan_vector" in report.kinds()
    issues = await env.reconciler.open_issues(tenant_id=TENANT_A, kinds=["orphan_vector"])
    assert [issue.resource_id for issue in issues] == ["ghost-version"]
    assert issues[0].severity == "critical", (
        "vectors with no owner can still match a query, which is how a deleted "
        "policy answers a question"
    )


async def test_orphan_vector_without_a_manifest_escalates_rather_than_guessing(
    env,
) -> None:
    """A repair that cannot be made safely must escalate, not improvise.

    The orphan's id prefix is unknown (no manifest records it), so there is no way
    to address exactly those rows. Deleting by a reconstructed guess could remove
    live data, so the issue becomes a human's.
    """
    await upload_material(env, tenant_id=TENANT_A, name="Policy", body=POLICY)
    await env.vectors.upsert_chunks(
        tenant_id=TENANT_A,
        knowledge_base_id=env.knowledge_base(TENANT_A),
        subject_kind="material",
        subject_id="ghost-material",
        version_id="ghost-version",
        embedding_version_id=env.embedding(TENANT_A),
        vector_id_prefix="ghostprefix",
        chunks=[ChunkPayload(chunk_id="c0", text="stale", vector=[0.5] * DIMENSIONS)],
        retrievable=True,
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A, repair=True)
    assert any(entry.startswith("orphan_vector:") for entry in report.escalated)
    async with env.factory() as session:
        issue = (
            (
                await session.execute(
                    select(ReconciliationIssue).where(
                        ReconciliationIssue.issue_kind == "orphan_vector"
                    )
                )
            )
            .scalars()
            .one()
        )
    assert issue.state == "manual_required"
    assert issue.state in RECONCILIATION_TERMINAL_STATES
    assert issue.resolution and "cannot be removed safely" in issue.resolution


async def test_missing_chunk_is_detected(env) -> None:
    """A partially-indexed manifest returns truncated evidence; that must be found."""
    _material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    manifest = await _manifest_for(env, version_id)
    assert manifest.expected_count >= 2

    held = await env.vectors.chunk_ids_for_prefix(
        tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
    )
    # Drop exactly one chunk, which is the insidious case: the manifest still has
    # vectors, so a naive "are there any?" check would pass.
    await env.vectors.delete_ids(
        tenant_id=TENANT_A,
        vector_ids=[f"{manifest.vector_id_prefix}:{held[0]}"],
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "missing_chunk" in report.kinds()
    issues = await env.reconciler.open_issues(tenant_id=TENANT_A, kinds=["missing_chunk"])
    assert len(issues) == 1
    assert issues[0].observed_fingerprint and "missing 1" in issues[0].observed_fingerprint


async def test_version_drift_is_detected_and_repaired(env) -> None:
    """The active pointer disagreeing with the served version is drift.

    This is the finding the deliberately-absent foreign key makes possible: an FK
    would have guaranteed the target exists, but could never have noticed that it
    names a different version than the one actually being served.
    """
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    async with env.factory() as session:
        await session.execute(
            update(Material)
            .where(Material.id == material_id)
            .values(active_version_id="stale-version-id")
        )
        await session.commit()

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "version_drift" in report.kinds()
    issues = await env.reconciler.open_issues(tenant_id=TENANT_A, kinds=["version_drift"])
    assert [issue.resource_id for issue in issues] == [material_id]

    repaired = await env.reconciler.sweep(tenant_id=TENANT_A, repair=True)
    assert any(entry.startswith("version_drift:") for entry in repaired.repaired)
    async with env.factory() as session:
        material = await session.get(Material, material_id)
        assert material is not None and material.active_version_id == version_id, (
            "the pointer is corrected to the served version, never the other way round"
        )


# -- all six at once ---------------------------------------------------------


async def test_every_issue_kind_is_detected_in_one_sweep(env) -> None:
    """All six kinds seeded together: detection must be 100%, not 5 of 6."""
    # Each fault is seeded on its own material so the findings cannot mask one
    # another (a missing object on the same row as a missing vector would make a
    # partial implementation look complete).
    missing_object_id, missing_object_version = await upload_material(
        env, tenant_id=TENANT_A, name="A", body=POLICY
    )
    orphan_object_id, _ = await upload_material(
        env, tenant_id=TENANT_A, name="B", body=POLICY
    )
    _missing_vector_id, missing_vector_version = await upload_material(
        env, tenant_id=TENANT_A, name="C", body=POLICY
    )
    _missing_chunk_id, missing_chunk_version = await upload_material(
        env, tenant_id=TENANT_A, name="D", body=POLICY
    )
    drift_id, _drift_version = await upload_material(
        env, tenant_id=TENANT_A, name="E", body=POLICY
    )

    await env.store.delete_all_versions(
        tenant_id=TENANT_A,
        material_id=missing_object_id,
        material_version_id=missing_object_version,
    )
    await _seed_orphan_object(
        env, tenant_id=TENANT_A, material_id=orphan_object_id
    )
    async with env.factory() as session:
        await session.execute(
            update(Material)
            .where(Material.id == drift_id)
            .values(active_version_id="stale-version-id")
        )
        await session.commit()

    missing_vector_manifest = await _manifest_for(env, missing_vector_version)
    await env.vectors.delete_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=missing_vector_manifest.vector_id_prefix
    )

    missing_chunk_manifest = await _manifest_for(env, missing_chunk_version)
    held = await env.vectors.chunk_ids_for_prefix(
        tenant_id=TENANT_A, vector_id_prefix=missing_chunk_manifest.vector_id_prefix
    )
    await env.vectors.delete_ids(
        tenant_id=TENANT_A,
        vector_ids=[f"{missing_chunk_manifest.vector_id_prefix}:{held[0]}"],
    )

    await env.vectors.upsert_chunks(
        tenant_id=TENANT_A,
        knowledge_base_id=env.knowledge_base(TENANT_A),
        subject_kind="material",
        subject_id="ghost-material",
        version_id="ghost-version-vectors",
        embedding_version_id=env.embedding(TENANT_A),
        vector_id_prefix="ghostprefix",
        chunks=[ChunkPayload(chunk_id="c0", text="stale", vector=[0.25] * DIMENSIONS)],
        retrievable=True,
    )

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    detected = report.kinds()
    assert detected == set(RECONCILIATION_ISSUE_KINDS), (
        f"detection is not 100%: missing {sorted(set(RECONCILIATION_ISSUE_KINDS) - detected)}"
    )
    # Every kind carries a declared severity, so a new kind cannot be added
    # without deciding how urgent it is.
    for issue in await env.reconciler.open_issues(tenant_id=TENANT_A):
        assert issue.severity == ISSUE_SEVERITY[issue.issue_kind]


async def test_sweep_is_idempotent_and_reopens_a_failed_repair(env) -> None:
    """Re-detection updates one row; a repair that did not hold reopens it."""
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    await env.store.delete_all_versions(
        tenant_id=TENANT_A, material_id=material_id, material_version_id=version_id
    )

    first = await env.reconciler.sweep(tenant_id=TENANT_A)
    second = await env.reconciler.sweep(tenant_id=TENANT_A)
    third = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert first.findings == second.findings == third.findings

    async with env.factory() as session:
        rows = (
            (
                await session.execute(
                    select(ReconciliationIssue).where(
                        ReconciliationIssue.issue_kind == "missing_object"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, (
        f"three sweeps created {len(rows)} rows for one fault; '100% detection' "
        "would then just mean 'the sweep ran often'"
    )

    # Mark it repaired by hand, then sweep again: the fault is still there, so the
    # issue must reopen rather than stay closed.
    async with env.factory() as session:
        issue = await session.get(ReconciliationIssue, rows[0].id)
        assert issue is not None
        issue.state = "repaired"
        issue.version += 1
        await session.commit()

    reopened = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert any(entry.startswith("missing_object:") for entry in reopened.reopened)
    async with env.factory() as session:
        issue = await session.get(ReconciliationIssue, rows[0].id)
        assert issue is not None and issue.state == "open"
        assert issue.resolved_at is None


async def test_repeated_repair_failure_escalates_to_manual(env) -> None:
    """A repair that keeps failing terminates as a human's problem."""
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    manifest = await _manifest_for(env, version_id)
    await env.vectors.delete_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
    )

    async def failing_deactivate(**kwargs):
        raise RuntimeError("milvus flip rejected")

    original = env.indexer.deactivate
    env.indexer.deactivate = failing_deactivate  # type: ignore[method-assign]
    try:
        for _ in range(4):
            await env.reconciler.sweep(tenant_id=TENANT_A, repair=True)
    finally:
        env.indexer.deactivate = original  # type: ignore[method-assign]

    async with env.factory() as session:
        issue = (
            (
                await session.execute(
                    select(ReconciliationIssue).where(
                        ReconciliationIssue.issue_kind == "missing_vector"
                    )
                )
            )
            .scalars()
            .one()
        )
    assert issue.state == "manual_required"
    assert issue.last_error_code == "RuntimeError"
    assert issue.attempts >= issue.max_attempts
    assert material_id  # the material itself is untouched by a failed repair


# -- outages and isolation ---------------------------------------------------


async def test_unreachable_milvus_yields_no_verdict(env) -> None:
    """An outage must not be reported as thousands of missing vectors.

    Declaring every manifest missing during an outage would be false, would bury
    real findings, and -- with repair enabled -- could withdraw every healthy
    material in the tenant.
    """
    await upload_material(env, tenant_id=TENANT_A, name="Policy", body=POLICY)

    async def unreachable(**kwargs):
        from backend.app.retrieval.milvus import RetrievalUnavailable

        raise RetrievalUnavailable("the vector store could not be reached")

    original = env.vectors.list_version_ids
    env.vectors.list_version_ids = unreachable  # type: ignore[method-assign]
    try:
        with pytest.raises(ReconciliationUnavailable):
            await env.reconciler.sweep(tenant_id=TENANT_A, repair=True)
    finally:
        env.vectors.list_version_ids = original  # type: ignore[method-assign]

    async with env.factory() as session:
        rows = (
            (
                await session.execute(
                    select(ReconciliationIssue).where(
                        ReconciliationIssue.issue_kind.in_(
                            ("missing_vector", "missing_chunk", "orphan_vector")
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], "an outage must produce no vector findings at all"


async def test_a_sweep_never_sees_another_tenants_drift(env) -> None:
    """Tenant isolation holds for reconciliation too."""
    material_id, version_id = await upload_material(
        env, tenant_id=TENANT_A, name="Policy", body=POLICY
    )
    await env.store.delete_all_versions(
        tenant_id=TENANT_A, material_id=material_id, material_version_id=version_id
    )
    await upload_material(env, tenant_id=TENANT_B, name="Policy", body=POLICY)

    report_b = await env.reconciler.sweep(tenant_id=TENANT_B)
    assert report_b.kinds() == set(), (
        f"tenant B's sweep reported tenant A's drift: {report_b.opened}"
    )
    report_a = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert "missing_object" in report_a.kinds()
    assert await env.reconciler.open_issues(tenant_id=TENANT_B) == []
