"""T075 [US1] object-store contract against a live versioned bucket.

These run against the real MinIO from ``infra/dev/compose.yaml`` and skip cleanly
when it is down. A fake would be worthless here: what Stage 5 needs proven is
precisely the *provider's* behaviour -- that it returns a distinct ``VersionId``
per PUT, that it keeps superseded versions readable, that a plain delete leaves a
delete marker behind, and that removing "every version and delete marker" really
empties the key. None of that is observable against a stub.

The invariants under test:

* a client is handed a short-lived, narrowly-scoped upload grant and never
  chooses a bucket or a key -- it addresses material and version IDs only;
* the key the service derives is opaque: it leaks no filename, tenant code or
  host path, and it is deterministic so the same version always resolves to the
  same object;
* an upload is only accepted after size, SHA-256, media type and scan status are
  verified against the bytes that actually landed;
* a range read returns exactly the requested window, so a large material can be
  streamed without loading it;
* ``delete_all_versions`` leaves no version and no delete marker;
* a caller presenting another tenant's material is refused before any request
  reaches the provider.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from backend.app.storage.object_store import (
    BUCKET_ALIAS_MATERIALS,
    CrossTenantObjectAccess,
    ObjectStore,
    ObjectStoreError,
    UploadVerificationError,
)

pytestmark = pytest.mark.asyncio

TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
async def store(object_store_config):
    """A store bound to the live bucket, cleaned up after the test."""
    instance = ObjectStore(object_store_config)
    await instance.verify_bucket_contract()
    issued: list[tuple[str, str, str]] = []
    original = instance.create_upload

    async def tracking_create_upload(**kwargs):
        grant = await original(**kwargs)
        issued.append((grant.tenant_id, grant.material_id, grant.material_version_id))
        return grant

    instance.create_upload = tracking_create_upload  # type: ignore[method-assign]
    try:
        yield instance
    finally:
        instance.create_upload = original  # type: ignore[method-assign]
        for tenant_id, material_id, material_version_id in issued:
            await instance.delete_all_versions(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=material_version_id,
            )
        await instance.close()


# -- upload grant ------------------------------------------------------------


async def test_upload_grant_is_scoped_and_short_lived(store: ObjectStore) -> None:
    """A grant names one object, expires soon and caps the byte count."""
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-1",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=1024,
    )
    assert grant.bucket_alias == BUCKET_ALIAS_MATERIALS
    assert grant.max_bytes == 1024
    lifetime = (grant.expires_at - datetime.now(UTC)).total_seconds()
    assert 0 < lifetime <= store.config.upload_grant_seconds, (
        "an upload grant must be short-lived; a long-lived URL is a standing "
        f"capability (got {lifetime}s)"
    )
    # The URL is a presigned PUT for exactly that key -- not a bucket-wide token.
    assert grant.object_key in grant.url
    assert grant.method == "PUT"


async def test_object_key_is_opaque_and_deterministic(store: ObjectStore) -> None:
    """The derived key leaks nothing and always resolves the same way."""
    first = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-1",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=16,
        filename="2026 Q3 Reimbursement Policy (confidential).docx",
    )
    again = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-1",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=16,
        filename="2026 Q3 Reimbursement Policy (confidential).docx",
    )
    assert first.object_key == again.object_key, "key derivation must be deterministic"

    key = first.object_key
    for leak in ("Reimbursement", "confidential", ".docx", TENANT_A, "mat-1", "ver-1"):
        assert leak.lower() not in key.lower(), f"the object key leaks {leak!r}"
    # Different versions of the same material must not collide.
    other_version = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-1",
        material_version_id="ver-2",
        media_type="text/plain",
        max_bytes=16,
    )
    assert other_version.object_key != key
    # Neither must the same IDs under a different tenant.
    other_tenant = await store.create_upload(
        tenant_id=TENANT_B,
        material_id="mat-1",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=16,
    )
    assert other_tenant.object_key != key


async def test_client_cannot_choose_bucket_or_key(store: ObjectStore) -> None:
    """``create_upload`` accepts IDs only; there is no bucket/key parameter."""
    import inspect

    # Introspect the class, not the instance: the fixture wraps the bound method
    # to track cleanup, and a wrapper's signature is not the contract under test.
    parameters = set(inspect.signature(ObjectStore.create_upload).parameters)
    for forbidden in ("bucket", "bucket_name", "object_key", "key", "prefix", "path"):
        assert forbidden not in parameters, (
            f"create_upload exposes {forbidden!r}; a client that can name its own "
            "object location can read or overwrite another tenant's bytes"
        )
    assert {"tenant_id", "material_id", "material_version_id"} <= parameters


# -- verification ------------------------------------------------------------


async def test_verify_upload_records_provider_version_and_metadata(
    store: ObjectStore,
) -> None:
    """A verified upload yields the provider's VersionId and the real metadata."""
    payload = b"reimbursement limit is 500 CNY per day"
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-verify",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=len(payload) * 4,
    )
    await store.put_for_test(grant, payload)

    stored = await store.verify_upload(
        grant=grant,
        expected_sha256=_sha256(payload),
        expected_size_bytes=len(payload),
        expected_media_type="text/plain",
    )
    assert stored.provider_version_id, "the provider VersionId must be recorded"
    assert stored.sha256 == _sha256(payload)
    assert stored.size_bytes == len(payload)
    assert stored.media_type == "text/plain"
    assert stored.scan_status == "clean"
    assert stored.object_key == grant.object_key


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        ({"expected_sha256": "f" * 64}, "sha256"),
        ({"expected_size_bytes": 999_999}, "size"),
        ({"expected_media_type": "application/pdf"}, "media_type"),
    ],
)
async def test_verify_upload_rejects_every_metadata_mismatch(
    store: ObjectStore, mutate: dict, field: str
) -> None:
    """Size, hash and media type are each verified against the stored bytes."""
    payload = b"per-diem is 200 CNY"
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-mismatch",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=4096,
    )
    await store.put_for_test(grant, payload)

    expected = {
        "expected_sha256": _sha256(payload),
        "expected_size_bytes": len(payload),
        "expected_media_type": "text/plain",
    }
    expected.update(mutate)
    with pytest.raises(UploadVerificationError) as caught:
        await store.verify_upload(grant=grant, **expected)
    assert field in str(caught.value)


async def test_verify_upload_rejects_missing_object(store: ObjectStore) -> None:
    """A grant that was never used must not verify as an upload."""
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-absent",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=16,
    )
    with pytest.raises(UploadVerificationError):
        await store.verify_upload(
            grant=grant,
            expected_sha256=_sha256(b""),
            expected_size_bytes=0,
            expected_media_type="text/plain",
        )


# -- reads -------------------------------------------------------------------


async def test_range_read_returns_exactly_the_window(store: ObjectStore) -> None:
    """A range read is byte-exact, so large materials can be streamed."""
    payload = b"0123456789abcdef"
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-range",
        material_version_id="ver-1",
        media_type="application/octet-stream",
        max_bytes=4096,
    )
    await store.put_for_test(grant, payload)
    stored = await store.verify_upload(
        grant=grant,
        expected_sha256=_sha256(payload),
        expected_size_bytes=len(payload),
        expected_media_type="application/octet-stream",
    )

    assert (
        await store.read_range(
            tenant_id=TENANT_A,
            material_id="mat-range",
            material_version_id="ver-1",
            provider_version_id=stored.provider_version_id,
            offset=4,
            length=6,
        )
        == payload[4:10]
    )
    # A window past the end clamps instead of failing.
    tail = await store.read_range(
        tenant_id=TENANT_A,
        material_id="mat-range",
        material_version_id="ver-1",
        provider_version_id=stored.provider_version_id,
        offset=12,
        length=100,
    )
    assert tail == payload[12:]


async def test_superseded_version_stays_readable_until_deleted(
    store: ObjectStore,
) -> None:
    """An update must not destroy the version a completed run already cited."""
    first_payload = b"limit 300"
    second_payload = b"limit 500"
    grant_one = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-super",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=4096,
    )
    await store.put_for_test(grant_one, first_payload)
    first = await store.verify_upload(
        grant=grant_one,
        expected_sha256=_sha256(first_payload),
        expected_size_bytes=len(first_payload),
        expected_media_type="text/plain",
    )
    # A second PUT at the same key (a re-upload of the same logical version)
    # creates a new provider version rather than overwriting the old bytes.
    await store.put_for_test(grant_one, second_payload)
    second = await store.verify_upload(
        grant=grant_one,
        expected_sha256=_sha256(second_payload),
        expected_size_bytes=len(second_payload),
        expected_media_type="text/plain",
    )
    assert first.provider_version_id != second.provider_version_id

    still_there = await store.read_range(
        tenant_id=TENANT_A,
        material_id="mat-super",
        material_version_id="ver-1",
        provider_version_id=first.provider_version_id,
        offset=0,
        length=len(first_payload),
    )
    assert still_there == first_payload, (
        "a superseded provider version must stay readable; evidence cites it by id"
    )


# -- physical deletion -------------------------------------------------------


async def test_delete_all_versions_leaves_no_version_or_delete_marker(
    store: ObjectStore,
) -> None:
    """Physical deletion removes every version *and* every delete marker.

    A plain delete on a versioned bucket only adds a delete marker: the bytes are
    still billable and still recoverable, so treating it as deletion would be a
    false claim.
    """
    payload = b"policy body"
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-delete",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=4096,
    )
    await store.put_for_test(grant, payload)
    await store.verify_upload(
        grant=grant,
        expected_sha256=_sha256(payload),
        expected_size_bytes=len(payload),
        expected_media_type="text/plain",
    )
    # A second version plus a delete marker, i.e. the worst realistic case.
    await store.put_for_test(grant, payload + b" v2")
    await store.soft_delete_for_test(grant)

    inventory = await store.inventory(
        tenant_id=TENANT_A, material_id="mat-delete", material_version_id="ver-1"
    )
    assert len(inventory.versions) >= 2
    assert inventory.delete_markers, "the test fixture must leave a delete marker"

    report = await store.delete_all_versions(
        tenant_id=TENANT_A, material_id="mat-delete", material_version_id="ver-1"
    )
    assert report.deleted_versions >= 2
    assert report.deleted_delete_markers >= 1

    after = await store.inventory(
        tenant_id=TENANT_A, material_id="mat-delete", material_version_id="ver-1"
    )
    assert after.versions == () and after.delete_markers == (), (
        f"physical deletion left {len(after.versions)} versions and "
        f"{len(after.delete_markers)} delete markers behind"
    )


async def test_delete_all_versions_is_idempotent(store: ObjectStore) -> None:
    """Re-running deletion on an already-empty key is a no-op, not an error.

    The saga retries ``deleting``, so a second pass after a partial failure must
    converge instead of raising.
    """
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-idem",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=4096,
    )
    await store.put_for_test(grant, b"x")
    await store.delete_all_versions(
        tenant_id=TENANT_A, material_id="mat-idem", material_version_id="ver-1"
    )
    repeat = await store.delete_all_versions(
        tenant_id=TENANT_A, material_id="mat-idem", material_version_id="ver-1"
    )
    assert repeat.deleted_versions == 0 and repeat.deleted_delete_markers == 0


# -- tenant isolation --------------------------------------------------------


async def test_cross_tenant_read_is_refused_before_reaching_the_provider(
    store: ObjectStore,
) -> None:
    """Tenant B cannot read a key derived for tenant A, even knowing the key."""
    payload = b"tenant A only"
    grant = await store.create_upload(
        tenant_id=TENANT_A,
        material_id="mat-iso",
        material_version_id="ver-1",
        media_type="text/plain",
        max_bytes=4096,
    )
    await store.put_for_test(grant, payload)
    stored = await store.verify_upload(
        grant=grant,
        expected_sha256=_sha256(payload),
        expected_size_bytes=len(payload),
        expected_media_type="text/plain",
    )

    # Tenant B has somehow learned the key (a log leak, a copied error message)
    # and presents it with its own tenant id: the derivation does not match, so
    # the request is refused before it reaches the provider.
    with pytest.raises(CrossTenantObjectAccess):
        await store.read_range(
            tenant_id=TENANT_B,
            material_id="mat-iso",
            material_version_id="ver-1",
            provider_version_id=stored.provider_version_id,
            offset=0,
            length=4,
            object_key=stored.object_key,
        )
    with pytest.raises(CrossTenantObjectAccess):
        await store.delete_all_versions(
            tenant_id=TENANT_B,
            material_id="mat-iso",
            material_version_id="ver-1",
            object_key=stored.object_key,
        )
    # Without the key, tenant B addresses an entirely different object. Tenant A's
    # VersionId does not exist there, so the provider refuses: either way the one
    # thing that must never happen is tenant B receiving tenant A's bytes.
    with pytest.raises(ObjectStoreError):
        await store.read_range(
            tenant_id=TENANT_B,
            material_id="mat-iso",
            material_version_id="ver-1",
            provider_version_id=stored.provider_version_id,
            offset=0,
            length=4,
        )
    # The owner is unaffected by the refused attempts.
    assert (
        await store.read_range(
            tenant_id=TENANT_A,
            material_id="mat-iso",
            material_version_id="ver-1",
            provider_version_id=stored.provider_version_id,
            offset=0,
            length=len(payload),
        )
        == payload
    )


async def test_bucket_contract_requires_versioning(object_store_config) -> None:
    """A bucket without versioning must be refused, not silently used.

    "Delete all versions and delete markers" cannot be honoured on an unversioned
    bucket, so starting against one would make the deletion guarantee a lie.
    """
    unversioned = object_store_config.model_copy(update={"bucket": "a-bucket"})
    store = ObjectStore(unversioned)
    try:
        with pytest.raises(Exception) as caught:
            await store.verify_bucket_contract()
        assert "versioning" in str(caught.value).lower()
    finally:
        await store.close()
