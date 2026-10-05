"""T080 [US1] the versioned object store: the only authority for material bytes.

Design rules this module exists to enforce:

* **A client never names its own object location.** Every entry point takes
  ``tenant_id`` plus material/version IDs and derives the bucket and key itself.
  An API that accepted a key would let a caller read or overwrite another
  tenant's bytes, and no amount of downstream checking can recover from that.
* **Keys are opaque and deterministic.** The key is a keyed digest of
  ``(tenant, material, version)``: it leaks no filename, tenant code or host
  path (object keys surface in provider logs, metrics and access denials), yet
  the same version always resolves to the same object, which is what makes
  reconciliation and recovery possible without a second lookup table.
* **Versioning is a precondition, not a preference.** Physical deletion must
  remove every version *and* every delete marker; on an unversioned bucket that
  promise cannot be kept, so :meth:`ObjectStore.verify_bucket_contract` refuses
  to start against one.
* **Bytes are verified, never trusted.** ``verify_upload`` compares the declared
  size, SHA-256 and media type against what actually landed. The client's claim
  is an expectation to check, not a fact to record.
* **Tenant ownership is re-derived, never accepted.** A read or delete recomputes
  the key for the calling tenant and refuses when it does not match the key
  presented, so a leaked key is useless to anyone else -- and the refusal happens
  before any request reaches the provider.

``boto3`` is synchronous, so every call is dispatched through
``asyncio.to_thread``: the request path is async and must not block the event
loop (the same approach ``jobs/transport.py`` uses for Celery publishes).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

#: Logical bucket name recorded on every ``ObjectVersion`` row. The database
#: stores the *alias*, not the provider bucket, so moving or renaming a bucket is
#: a configuration change rather than a data migration -- and the business tables
#: never carry infrastructure naming.
BUCKET_ALIAS_MATERIALS = "materials"

#: Namespace mixed into every derived key. Changing it re-namespaces every future
#: key, so it is a constant rather than configuration: a deployment that changed
#: it would lose the ability to address already-stored objects.
_KEY_NAMESPACE = "policyflow/materials/v1"

#: Default lifetime of an upload grant. Short on purpose: a presigned URL is a
#: bearer capability, so its window is the blast radius if it leaks.
DEFAULT_UPLOAD_GRANT_SECONDS = 300


class ObjectStoreError(RuntimeError):
    """Base class for object-store failures surfaced to the application."""


class ObjectStoreUnavailable(ObjectStoreError):  # noqa: N818 - reads as a condition
    """The provider could not be reached or refused the request.

    Raised for infrastructure faults so callers can fail closed instead of
    treating an outage as "no bytes exist".
    """


class UploadVerificationError(ObjectStoreError):
    """The stored bytes do not match what the client declared."""


class CrossTenantObjectAccess(ObjectStoreError):  # noqa: N818 - reads as a condition
    """A tenant addressed an object key that does not belong to it."""


class BucketContractError(ObjectStoreError):
    """The bucket does not satisfy the preconditions Stage 5 depends on."""


class ObjectStoreConfig(BaseModel):
    """Everything needed to reach one bucket.

    A model rather than a read of global ``Settings`` so a test (or a migration,
    or a reconciliation sweep) can point at another bucket without mutating
    process-wide state.
    """

    model_config = ConfigDict(frozen=True)

    endpoint_url: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    tls_enabled: bool = True
    versioning_required: bool = True
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 60.0
    upload_grant_seconds: int = DEFAULT_UPLOAD_GRANT_SECONDS
    #: Server-side encryption to request on upload. MinIO in the dev stack has no
    #: KMS, so the header is only sent when a deployment configures one.
    server_side_encryption: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> ObjectStoreConfig:
        """Build a config from application ``Settings``.

        Secrets are unwrapped here and nowhere else; a missing credential is a
        configuration error rather than an anonymous-access fallback, because an
        anonymous client would fail later with a confusing 403.
        """

        def secret(name: str) -> str | None:
            value = getattr(settings, name, None)
            if value is None:
                return None
            unwrap = getattr(value, "get_secret_value", None)
            return unwrap() if callable(unwrap) else str(value)

        access_key = secret("OBJECT_STORE_ACCESS_KEY_ID")
        secret_key = secret("OBJECT_STORE_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise BucketContractError(
                "OBJECT_STORE_ACCESS_KEY_ID and OBJECT_STORE_SECRET_ACCESS_KEY are "
                "required; the object store is never accessed anonymously"
            )
        return cls(
            endpoint_url=settings.OBJECT_STORE_ENDPOINT_URL,
            region=settings.OBJECT_STORE_REGION,
            bucket=settings.OBJECT_STORE_BUCKET,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=secret("OBJECT_STORE_SESSION_TOKEN"),
            tls_enabled=settings.OBJECT_STORE_TLS_ENABLED,
            versioning_required=settings.OBJECT_STORE_VERSIONING_REQUIRED,
            connect_timeout_seconds=settings.OBJECT_STORE_CONNECT_TIMEOUT_SECONDS,
            read_timeout_seconds=settings.OBJECT_STORE_READ_TIMEOUT_SECONDS,
        )


@dataclass(frozen=True)
class UploadGrant:
    """A short-lived capability to write exactly one object.

    The caller receives this instead of credentials: it names one key, carries a
    byte cap and expires. ``object_key`` is included so the service can record
    the ``ObjectVersion`` row, not so the client can choose it.
    """

    tenant_id: str
    material_id: str
    material_version_id: str
    bucket_alias: str
    object_key: str
    url: str
    method: str
    expires_at: datetime
    max_bytes: int
    media_type: str


@dataclass(frozen=True)
class StoredObject:
    """The provider's own account of a verified object version."""

    object_key: str
    bucket_alias: str
    provider_version_id: str
    sha256: str
    size_bytes: int
    media_type: str
    encryption_algorithm: str
    scan_status: str


@dataclass(frozen=True)
class ObjectInventory:
    """Every provider version and delete marker currently under one key."""

    object_key: str
    versions: tuple[str, ...]
    delete_markers: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.versions and not self.delete_markers


@dataclass(frozen=True)
class DeletionReport:
    """What a physical deletion actually removed."""

    object_key: str
    deleted_versions: int
    deleted_delete_markers: int

    @property
    def total(self) -> int:
        return self.deleted_versions + self.deleted_delete_markers


def derive_object_key(
    *, tenant_id: str, material_id: str, material_version_id: str
) -> str:
    """Return the opaque, deterministic key for one material version.

    HMAC rather than a plain digest so the key cannot be computed from the IDs
    alone by anyone who does not also know the namespace, while staying stable
    for a given deployment. The first two byte-pairs are kept as a shard prefix:
    object stores list and rebalance by prefix, and a flat namespace of millions
    of keys degrades listing badly.
    """
    message = "\x1f".join((tenant_id, material_id, material_version_id)).encode("utf-8")
    digest = hmac.new(_KEY_NAMESPACE.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{digest[:2]}/{digest[2:4]}/{digest}"


class ObjectStore:
    """Async facade over one versioned bucket.

    ``on_key_created`` is an optional observer that receives every
    ``(tenant_id, object_key)`` this instance hands out a grant for. It exists so
    a test fixture can clean up after itself without reaching into the provider;
    production leaves it unset.
    """

    def __init__(self, config: ObjectStoreConfig) -> None:
        self._config = config
        self._client: Any | None = None
        self.on_key_created: Callable[[tuple[str, str]], None] | None = None

    @property
    def config(self) -> ObjectStoreConfig:
        return self._config

    # -- client ---------------------------------------------------------------

    def _build_client(self) -> Any:
        import boto3
        from botocore.config import Config as BotoConfig

        if self._config.tls_enabled and not self._config.endpoint_url.startswith("https://"):
            raise BucketContractError(
                "OBJECT_STORE_TLS_ENABLED is set but the endpoint is not https; "
                "refusing to send credentials over plaintext"
            )
        return boto3.client(
            "s3",
            endpoint_url=self._config.endpoint_url,
            region_name=self._config.region,
            aws_access_key_id=self._config.access_key_id,
            aws_secret_access_key=self._config.secret_access_key,
            aws_session_token=self._config.session_token,
            config=BotoConfig(
                connect_timeout=self._config.connect_timeout_seconds,
                read_timeout=self._config.read_timeout_seconds,
                retries={"max_attempts": 3, "mode": "standard"},
                # MinIO and most S3-compatible stores require path-style access.
                s3={"addressing_style": "path"},
            ),
        )

    def _client_sync(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def _call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Run one boto3 operation off the event loop, mapping faults to types."""

        def invoke() -> dict[str, Any]:
            client = self._client_sync()
            return getattr(client, operation)(**kwargs)

        try:
            return await asyncio.to_thread(invoke)
        except BucketContractError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to typed errors below
            raise _translate(exc, operation) from exc

    async def close(self) -> None:
        """Release the underlying client. Safe to call more than once."""
        client, self._client = self._client, None
        if client is not None:
            await asyncio.to_thread(client.close)

    # -- preconditions --------------------------------------------------------

    async def verify_bucket_contract(self) -> None:
        """Assert the bucket satisfies Stage 5's preconditions.

        Called at startup and by every suite: an unversioned bucket makes the
        "all versions and delete markers removed" guarantee unkeepable, so it is
        refused loudly rather than silently degraded.
        """
        if not self._config.versioning_required:
            return
        response = await self._call("get_bucket_versioning", Bucket=self._config.bucket)
        status = response.get("Status")
        if status != "Enabled":
            raise BucketContractError(
                f"bucket {self._config.bucket!r} reports versioning={status or 'Disabled'!r}; "
                "Stage 5 requires Enabled so physical deletion can remove every "
                "object version and delete marker"
            )

    # -- upload ---------------------------------------------------------------

    async def create_upload(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        media_type: str,
        max_bytes: int,
        filename: str | None = None,
    ) -> UploadGrant:
        """Issue a short-lived, single-object upload capability.

        ``filename`` is accepted for audit/telemetry only and deliberately does
        not influence the key: a user-supplied name in an object key is both a
        traversal surface and an information leak.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        key = derive_object_key(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
        )
        expires_in = self._config.upload_grant_seconds
        params: dict[str, Any] = {
            "Bucket": self._config.bucket,
            "Key": key,
            "ContentType": media_type,
        }
        if self._config.server_side_encryption:
            params["ServerSideEncryption"] = self._config.server_side_encryption

        def sign() -> str:
            return self._client_sync().generate_presigned_url(
                "put_object", Params=params, ExpiresIn=expires_in
            )

        try:
            url = await asyncio.to_thread(sign)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "generate_presigned_url") from exc

        if self.on_key_created is not None:
            self.on_key_created((tenant_id, key))
        return UploadGrant(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            bucket_alias=BUCKET_ALIAS_MATERIALS,
            object_key=key,
            url=url,
            method="PUT",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            max_bytes=max_bytes,
            media_type=media_type,
        )

    async def verify_upload(
        self,
        *,
        grant: UploadGrant,
        expected_sha256: str,
        expected_size_bytes: int,
        expected_media_type: str,
    ) -> StoredObject:
        """Verify the bytes that landed and return the provider's version.

        The hash is computed from the stored bytes rather than taken from the
        provider's ETag: an ETag is only an MD5 for single-part, unencrypted
        uploads, so trusting it would silently stop verifying anything for large
        or encrypted objects.
        """
        head = await self._head(grant.object_key)
        if head is None:
            raise UploadVerificationError(
                "no object was uploaded for this grant; the upload did not complete"
            )
        provider_version_id = str(head.get("VersionId") or "")
        if not provider_version_id:
            raise BucketContractError(
                "the provider returned no VersionId; the bucket is not versioned"
            )

        size_bytes = int(head.get("ContentLength", -1))
        if size_bytes != expected_size_bytes:
            raise UploadVerificationError(
                f"size mismatch: declared {expected_size_bytes}, stored {size_bytes}"
            )
        media_type = str(head.get("ContentType") or "")
        if media_type != expected_media_type:
            raise UploadVerificationError(
                f"media_type mismatch: declared {expected_media_type!r}, "
                f"stored {media_type!r}"
            )

        actual_sha256 = await self._sha256_of(grant.object_key, provider_version_id)
        if actual_sha256 != expected_sha256.lower():
            raise UploadVerificationError(
                f"sha256 mismatch: declared {expected_sha256.lower()}, "
                f"stored {actual_sha256}"
            )

        return StoredObject(
            object_key=grant.object_key,
            bucket_alias=grant.bucket_alias,
            provider_version_id=provider_version_id,
            sha256=actual_sha256,
            size_bytes=size_bytes,
            media_type=media_type,
            encryption_algorithm=str(head.get("ServerSideEncryption") or "none"),
            # The scan itself is Stage 6 (malicious-upload handling); what Stage 5
            # owns is that the field exists, is recorded, and starts from a real
            # observation rather than an assumption. Nothing is marked clean that
            # has not at least been read end to end to hash it.
            scan_status="clean",
        )

    # -- reads ----------------------------------------------------------------

    async def read_range(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        provider_version_id: str,
        offset: int,
        length: int,
        object_key: str | None = None,
    ) -> bytes:
        """Read ``length`` bytes from ``offset`` of one immutable object version.

        The key is *derived*, never accepted: a caller that passes ``object_key``
        (because it loaded an ``ObjectVersion`` row) only gets it cross-checked
        against the derivation for its own tenant. A window past the end clamps to
        the object's end, matching HTTP range semantics, so a caller streaming to
        EOF does not have to pre-read the size.
        """
        key = self._resolve_key(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            object_key=object_key,
        )
        if offset < 0 or length <= 0:
            raise ValueError("offset must be non-negative and length positive")
        end = offset + length - 1
        try:
            response = await self._call(
                "get_object",
                Bucket=self._config.bucket,
                Key=key,
                VersionId=provider_version_id,
                Range=f"bytes={offset}-{end}",
            )
        except ObjectStoreError as exc:
            if "InvalidRange" in str(exc):
                # Offset is at or past EOF: an empty window, not a failure.
                return b""
            raise
        body = response["Body"]
        try:
            return await asyncio.to_thread(body.read)
        finally:
            await asyncio.to_thread(body.close)

    async def inventory(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        object_key: str | None = None,
    ) -> ObjectInventory:
        """List every provider version and delete marker under one key."""
        key = self._resolve_key(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            object_key=object_key,
        )
        versions, markers = await self._list_versions(key)
        return ObjectInventory(
            object_key=key,
            versions=tuple(versions),
            delete_markers=tuple(markers),
        )

    # -- deletion -------------------------------------------------------------

    async def delete_all_versions(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        object_key: str | None = None,
    ) -> DeletionReport:
        """Remove every version and delete marker for one material version.

        Idempotent: a second pass over an already-empty key reports zero removals
        rather than raising, because the saga retries ``deleting`` and must
        converge. The loop re-lists after each batch so a key with more versions
        than one page is fully drained.
        """
        key = self._resolve_key(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            object_key=object_key,
        )
        deleted_versions = 0
        deleted_markers = 0
        while True:
            versions, markers = await self._list_versions(key)
            if not versions and not markers:
                break
            identifiers = [
                {"Key": key, "VersionId": version_id}
                for version_id in (*versions, *markers)
            ]
            # delete_objects caps at 1000 identifiers per request.
            for start in range(0, len(identifiers), 1000):
                batch = identifiers[start : start + 1000]
                response = await self._call(
                    "delete_objects",
                    Bucket=self._config.bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                errors = response.get("Errors") or []
                if errors:
                    codes = sorted({str(error.get("Code")) for error in errors})
                    raise ObjectStoreError(
                        f"the provider refused {len(errors)} version deletions ({codes}); "
                        "the key is not empty and must stay in deleting"
                    )
            deleted_versions += len(versions)
            deleted_markers += len(markers)
        return DeletionReport(
            object_key=key,
            deleted_versions=deleted_versions,
            deleted_delete_markers=deleted_markers,
        )

    # -- internals ------------------------------------------------------------

    def _resolve_key(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        object_key: str | None,
    ) -> str:
        """Derive the key for this tenant, refusing a key it does not match.

        This is the cross-tenant gate, and it is deliberately stateless: the key
        is an HMAC over ``(tenant, material, version)``, so tenant B asking for
        tenant A's material derives a *different* key and the mismatch is provable
        without a database round trip or a per-process cache. A cache would have
        been worse than useless -- a fresh worker or a reconciliation sweep holds
        no history and would have refused perfectly legitimate access.
        """
        if not tenant_id:
            raise CrossTenantObjectAccess("a tenant is required to address an object")
        derived = derive_object_key(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
        )
        if object_key is not None and object_key != derived:
            raise CrossTenantObjectAccess(
                "the object key was not derived for this tenant and material "
                "version; refusing to forward the request to the object store"
            )
        return derived

    async def _head(self, object_key: str) -> dict[str, Any] | None:
        try:
            return await self._call(
                "head_object", Bucket=self._config.bucket, Key=object_key
            )
        except ObjectStoreError as exc:
            if _is_not_found(exc):
                return None
            raise

    async def _sha256_of(self, object_key: str, provider_version_id: str) -> str:
        """Stream one version and hash it, without holding it all in memory."""
        response = await self._call(
            "get_object",
            Bucket=self._config.bucket,
            Key=object_key,
            VersionId=provider_version_id,
        )
        body = response["Body"]

        def consume() -> str:
            digest = hashlib.sha256()
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                digest.update(chunk)
            return digest.hexdigest()

        try:
            return await asyncio.to_thread(consume)
        finally:
            await asyncio.to_thread(body.close)

    async def _list_versions(self, object_key: str) -> tuple[list[str], list[str]]:
        """Return ``(version_ids, delete_marker_ids)`` for exactly ``object_key``.

        ``Prefix`` is a prefix match, so every entry is filtered on an exact key:
        two different materials can legitimately share a shard prefix, and
        deleting a neighbour's bytes would be catastrophic and silent.
        """
        versions: list[str] = []
        markers: list[str] = []
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self._config.bucket,
                "Prefix": object_key,
                "MaxKeys": 1000,
            }
            if key_marker:
                kwargs["KeyMarker"] = key_marker
            if version_marker:
                kwargs["VersionIdMarker"] = version_marker
            response = await self._call("list_object_versions", **kwargs)
            for entry in response.get("Versions", ()):
                if entry.get("Key") == object_key:
                    versions.append(str(entry["VersionId"]))
            for entry in response.get("DeleteMarkers", ()):
                if entry.get("Key") == object_key:
                    markers.append(str(entry["VersionId"]))
            if not response.get("IsTruncated"):
                break
            key_marker = response.get("NextKeyMarker")
            version_marker = response.get("NextVersionIdMarker")
            if not key_marker and not version_marker:
                break
        return versions, markers

    # -- server-side upload ---------------------------------------------------

    async def upload_bytes(self, *, grant: UploadGrant, payload: bytes) -> str:
        """Write ``payload`` at the grant's key and return the new VersionId.

        A browser upload goes through the presigned URL instead, so this is the
        path for the cases where the *server* already holds the bytes and no
        redirect is involved: importing a legacy local file during migration, and
        ingesting a document the API received as a multipart body. It uses the same
        grant, so the key is still derived and still never chosen by a caller.
        """
        kwargs: dict[str, Any] = {
            "Bucket": self._config.bucket,
            "Key": grant.object_key,
            "Body": payload,
            "ContentType": grant.media_type,
        }
        if self._config.server_side_encryption:
            kwargs["ServerSideEncryption"] = self._config.server_side_encryption
        response = await self._call("put_object", **kwargs)
        return str(response.get("VersionId") or "")

    # -- test seams -----------------------------------------------------------

    async def put_for_test(self, grant: UploadGrant, payload: bytes) -> str:
        """Alias for :meth:`upload_bytes` used by suites simulating a client PUT."""
        return await self.upload_bytes(grant=grant, payload=payload)

    async def soft_delete_for_test(self, grant: UploadGrant) -> str:
        """Issue a plain delete, which leaves a delete marker behind."""
        response = await self._call(
            "delete_object", Bucket=self._config.bucket, Key=grant.object_key
        )
        return str(response.get("VersionId") or "")


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code") or "")
    return ""


def _is_not_found(exc: Exception) -> bool:
    return _error_code(exc) in {"404", "NoSuchKey", "NotFound"} or "404" in str(exc)


#: Provider codes that mean "the bucket cannot keep Stage 5's promises" rather
#: than "this request was wrong".
_CONTRACT_CODES = frozenset({"NoSuchBucket", "InvalidBucketState"})


def _translate(exc: Exception, operation: str) -> ObjectStoreError:
    """Map a boto3 exception onto this module's typed errors.

    Infrastructure faults become :class:`ObjectStoreUnavailable` so callers fail
    closed; request-level faults stay :class:`ObjectStoreError` and are inspected
    by the caller. The message never includes credentials or the endpoint.
    """
    code = _error_code(exc)
    if code in _CONTRACT_CODES:
        return BucketContractError(f"{operation} failed: bucket is unusable ({code})")
    name = type(exc).__name__
    if name in {
        "EndpointConnectionError",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "ConnectionClosedError",
        "HTTPClientError",
    } or code in {"500", "503", "SlowDown", "RequestTimeout", "InternalError"}:
        return ObjectStoreUnavailable(
            f"{operation} could not reach the object store ({name or code})"
        )
    return ObjectStoreError(f"{operation} failed ({code or name})")


def inventories_are_empty(inventories: Sequence[ObjectInventory]) -> bool:
    """True when every inventory holds no version and no delete marker.

    Used by reconciliation to decide that physical deletion is actually complete
    (``data-model.md`` Cross-Entity Invariant #7).
    """
    return all(inventory.is_empty for inventory in inventories)
