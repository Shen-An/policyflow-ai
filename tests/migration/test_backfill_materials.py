"""T087 [US1] the local-file backfill, verified against live MinIO and Milvus.

Skips cleanly when the stores are down. A fake cannot prove the thing that matters
here: that the bytes which arrive in versioned object storage are *the same bytes*
the local file held, confirmed by byte count and SHA-256 through the same
verification production uses.

What is proven:

* a clean document is migrated: object version recorded, hash and size match, and
  a Milvus manifest exists but is **not retrievable** -- a half-migrated knowledge
  base must not start answering;
* the authority pointer flips only with ``--activate`` and only after verification,
  so until then readers still see the legacy path;
* a local file whose bytes disagree with the recorded ``content_hash`` is reported
  and skipped, never imported -- importing it would launder a corrupted or tampered
  file into the store evidence is cited from;
* a missing or empty file is reported, not silently treated as success;
* the run is restartable: a second pass skips what it already verified instead of
  duplicating it;
* nothing is destroyed -- the local file is still there afterwards, because
  deleting it is a separate Stage 9 step gated on the zero-use counter.
"""

from __future__ import annotations

import hashlib

import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models import (
    Material,
    MaterialVersion,
    ObjectVersion,
    VectorManifest,
)
from migrations.backfill_materials import (
    SKIP_ALREADY_MIGRATED,
    SKIP_EMPTY,
    SKIP_HASH_MISMATCH,
    SKIP_MISSING_FILE,
    MaterialBackfill,
)
from tests.stage5_env import DIMENSIONS, TENANT_A, stage5_environment

POLICY = "daily limit is 300 CNY\nreceipts above 100 CNY\nmanager approval over 2000\n"


@pytest_asyncio.fixture
async def env(pg_url: str, milvus_uri: str, object_store_config, unique_suffix):
    async with stage5_environment(
        pg_url=pg_url,
        milvus_uri=milvus_uri,
        object_store_config=object_store_config,
        scratch_name="pf_mig_backfill",
        collection_suffix=unique_suffix("backfill"),
    ) as environment:
        yield environment


@pytest_asyncio.fixture
async def backfill(env) -> MaterialBackfill:
    return MaterialBackfill(
        factory=env.factory,
        object_store=env.store,
        indexer=env.indexer,
        dimensions=DIMENSIONS,
    )


async def _legacy_document(
    env,
    tmp_path,
    *,
    title: str,
    body: str = POLICY,
    content_hash: str | None = None,
    write_file: bool = True,
):
    """Create a pre-Stage-5 KnowledgeDocument with a real file on disk."""
    from backend.app.db.models import KnowledgeDocument

    path = tmp_path / f"{title}.txt"
    if write_file:
        # Written as bytes, not text: ``write_text`` applies the platform's newline
        # translation, so on Windows the on-disk bytes would differ from the hash
        # recorded below and the backfill would (correctly) refuse them. That is a
        # real-world corruption mode, but here it would just be a test bug.
        path.write_bytes(body.encode("utf-8"))
    async with env.factory() as session:
        document = KnowledgeDocument(
            tenant_id=TENANT_A,
            knowledge_base_id=env.knowledge_base(TENANT_A),
            title=title,
            file_path=str(path),
            file_type="txt",
            content_text=body,
            content_hash=(
                content_hash
                if content_hash is not None
                else hashlib.sha256(body.encode("utf-8")).hexdigest()
            ),
            created_by="user-0",
        )
        session.add(document)
        await session.commit()
        return document.id, path


# -- the clean path ----------------------------------------------------------


async def test_migrates_bytes_with_hash_and_size_verification(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """The object version records exactly the bytes the local file held."""
    document_id, path = await _legacy_document(env, tmp_path, title="Reimbursement")

    report = await backfill.run(tenant_id=TENANT_A)
    assert report.scanned == 1 and report.migrated == 1, report.to_json()
    outcome = report.outcomes[0]
    assert outcome.document_id == document_id
    assert outcome.sha256 == hashlib.sha256(POLICY.encode("utf-8")).hexdigest()
    assert outcome.size_bytes == len(POLICY.encode("utf-8"))

    async with env.factory() as session:
        version = await session.get(MaterialVersion, outcome.material_version_id)
        assert version is not None
        assert version.object_version_id == outcome.object_version_id
        assert version.sha256 == outcome.sha256
        assert version.size_bytes == outcome.size_bytes
        object_row = await session.get(ObjectVersion, outcome.object_version_id)
        assert object_row is not None
        assert object_row.provider_version_id, "the provider VersionId must be recorded"
        material = await session.get(Material, outcome.material_id)
        assert material is not None
        assert material.read_only is True, (
            "a migrated knowledge document is a formal policy original"
        )

    # The bytes really are readable back out of the versioned store.
    fetched = await env.store.read_range(
        tenant_id=TENANT_A,
        material_id=outcome.material_id,
        material_version_id=outcome.material_version_id,
        provider_version_id=object_row.provider_version_id,
        offset=0,
        length=outcome.size_bytes,
    )
    assert fetched == POLICY.encode("utf-8")
    # Non-destructive: removing the local file is a separate Stage 9 step.
    assert path.is_file(), "the backfill must not delete the local file"


async def test_manifest_is_built_but_not_retrievable_without_activate(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """A half-migrated knowledge base must not start answering."""
    await _legacy_document(env, tmp_path, title="Travel")

    report = await backfill.run(tenant_id=TENANT_A)
    outcome = report.outcomes[0]
    assert outcome.expected_chunks > 0
    assert outcome.indexed_chunks == outcome.expected_chunks, (
        "the manifest must be verified against Milvus even when not activated"
    )
    assert outcome.retrievable is False
    assert report.activated == 0

    async with env.factory() as session:
        manifest = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_version_id == outcome.material_version_id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert manifest.retrievable is False

    scope = await env.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(env.knowledge_base(TENANT_A),),
        embedding_version_id=env.embedding(TENANT_A),
    )
    assert scope.version_ids == (), (
        "a backfilled-but-unactivated version must not be in the retrieval scope"
    )


async def test_activate_switches_the_pointer_only_after_verification(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """With --activate the CAS flip happens, and only then is it served."""
    await _legacy_document(env, tmp_path, title="Expense")

    report = await backfill.run(tenant_id=TENANT_A, activate=True)
    outcome = report.outcomes[0]
    assert outcome.retrievable is True and report.activated == 1

    async with env.factory() as session:
        material = await session.get(Material, outcome.material_id)
        assert material is not None
        assert material.status == "available"
        assert material.active_version_id == outcome.material_version_id

    scope = await env.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(env.knowledge_base(TENANT_A),),
        embedding_version_id=env.embedding(TENANT_A),
    )
    assert scope.version_ids == (outcome.material_version_id,)

    # And the sweep agrees the result is internally consistent.
    sweep = await env.reconciler.sweep(tenant_id=TENANT_A)
    assert sweep.kinds() == set(), f"the backfill left drift behind: {sweep.opened}"


# -- refusals ----------------------------------------------------------------


async def test_a_file_that_disagrees_with_its_recorded_hash_is_skipped(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """Never import bytes that do not match what the database claimed."""
    await _legacy_document(
        env, tmp_path, title="Tampered", content_hash="f" * 64
    )

    report = await backfill.run(tenant_id=TENANT_A)
    assert report.migrated == 0 and report.skipped == 1
    outcome = report.outcomes[0]
    assert outcome.detail == SKIP_HASH_MISMATCH
    assert outcome.sha256 == hashlib.sha256(POLICY.encode("utf-8")).hexdigest(), (
        "the report must show what the bytes actually hash to"
    )

    async with env.factory() as session:
        rows = (
            (await session.execute(select(ObjectVersion))).scalars().all()
        )
        assert rows == [], "nothing may be written for a rejected document"


async def test_a_missing_local_file_is_reported_not_silently_succeeded(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    await _legacy_document(env, tmp_path, title="Gone", write_file=False)
    report = await backfill.run(tenant_id=TENANT_A)
    assert report.migrated == 0 and report.skipped == 1
    assert report.outcomes[0].detail == SKIP_MISSING_FILE


async def test_an_empty_local_file_is_reported(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """A zero-byte material has no evidence value and no valid declared size."""
    await _legacy_document(env, tmp_path, title="Empty", body="")
    report = await backfill.run(tenant_id=TENANT_A)
    assert report.migrated == 0 and report.skipped == 1
    assert report.outcomes[0].detail == SKIP_EMPTY


# -- restartability ----------------------------------------------------------


async def test_a_second_pass_skips_what_it_already_verified(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """An interrupted backfill resumes instead of duplicating."""
    await _legacy_document(env, tmp_path, title="Restartable")

    first = await backfill.run(tenant_id=TENANT_A)
    assert first.migrated == 1
    second = await backfill.run(tenant_id=TENANT_A)
    assert second.migrated == 0 and second.skipped == 1
    assert second.outcomes[0].detail == SKIP_ALREADY_MIGRATED
    assert second.outcomes[0].material_version_id == first.outcomes[0].material_version_id

    async with env.factory() as session:
        versions = (
            (await session.execute(select(MaterialVersion))).scalars().all()
        )
        assert len(versions) == 1, (
            f"a second pass created {len(versions)} versions for one document"
        )
        objects = (await session.execute(select(ObjectVersion))).scalars().all()
        assert len(objects) == 1


async def test_report_is_serialisable_evidence(
    env, backfill: MaterialBackfill, tmp_path
) -> None:
    """The run produces a JSON report that can be stored as migration evidence."""
    import json

    await _legacy_document(env, tmp_path, title="Evidence")
    report = await backfill.run(tenant_id=TENANT_A)
    payload = report.to_json()
    rendered = json.dumps(payload)
    assert json.loads(rendered)["migrated"] == 1
    assert set(payload) == {
        "scanned",
        "migrated",
        "skipped",
        "failed",
        "activated",
        "outcomes",
    }
    # Per-document rows carry the byte count and hash that justify the claim.
    assert payload["outcomes"][0]["sha256"]
    assert payload["outcomes"][0]["size_bytes"] > 0
