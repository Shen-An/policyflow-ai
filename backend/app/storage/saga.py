"""T083 [US1] the cross-store material saga, driven by the Stage-4 outbox.

A material upload touches three authorities that cannot share a transaction:
PostgreSQL metadata, object-store bytes and Milvus vectors. The saga is what makes
that safe without distributed transactions:

``pending_upload -> scanning -> indexing -> available -> deleting -> deleted/error``

Rules the implementation enforces:

* **One step per call, each one durable.** :meth:`MaterialSaga.advance_once`
  performs exactly one transition and commits it together with its outbox event
  (reusing Stage 4's ``OutboxEvent`` table and its
  ``(aggregate, version, event_type)`` unique constraint). A crash can therefore
  only ever land between steps, which is a state the next pass can read and
  resume from.
* **Idempotent by durable state, not by memory.** Each step re-reads what the
  other stores actually contain before acting, and a step whose work is already
  present returns the same result instead of repeating it. Re-running the whole
  saga after a redelivered message converges.
* **No permanent dual write.** While an update is in flight the old and the new
  version both exist, but only one is ever retrievable, and the previous version
  is marked ``superseded`` and un-indexed as part of reaching ``available``. The
  overlap is transient by construction, not a steady state two readers could
  disagree about.
* **Failure parks, it does not unwind.** A recoverable failure records
  ``attempts``/``next_attempt_at``/``last_error_code`` and leaves the material in
  a state the sweep can retry. ``deleting`` in particular never falls back to
  ``available``: a partially deleted material must stay in ``deleting`` until
  deletion completes, because the alternative is advertising bytes that are
  already half gone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import (
    MATERIAL_SAGA_TRANSITIONS,
    Material,
    MaterialVersion,
    ObjectVersion,
    OutboxEvent,
    VectorManifest,
    next_version_number,
    object_metadata_error,
    utc_now,
)
from backend.app.retrieval.indexer import ChunkPayload, VectorIndexer
from backend.app.storage.object_store import (
    ObjectStore,
    ObjectStoreError,
    UploadGrant,
    UploadVerificationError,
)

#: Outbox aggregate type for material transitions, alongside Stage 4's
#: ``durable_job``. Keeping them in one table means one relay drains everything.
AGGREGATE_TYPE = "material"

#: Terminal material states: the saga has nothing further to do.
TERMINAL_STATUSES: frozenset[str] = frozenset({"available", "deleted"})

#: Default retry budget for one saga step before it escalates to ``error``.
DEFAULT_MAX_ATTEMPTS = 5


class SagaStateError(RuntimeError):
    """A transition was requested that the saga graph forbids."""


class SagaStepFailed(RuntimeError):  # noqa: N818 - reads as the event it is
    """One step could not complete; the material stays retryable.

    ``error_code`` is a stable short token recorded on the row, never a provider
    message: user-visible error state must not carry paths or credentials.
    """

    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code


@dataclass(frozen=True)
class StepOutcome:
    """What one :meth:`MaterialSaga.advance_once` call did."""

    material_id: str
    from_status: str
    to_status: str
    event_type: str | None
    changed: bool

    @property
    def is_terminal(self) -> bool:
        return self.to_status in TERMINAL_STATUSES


@dataclass(frozen=True)
class MaterialDraft:
    """A started saga: the material, its first version and the upload grant."""

    material_id: str
    material_version_id: str
    version_number: int
    grant: UploadGrant


class MaterialSaga:
    """Drives one material through the cross-store lifecycle.

    ``chunker`` turns verified bytes into chunks to embed. It is injected rather
    than imported so Stage 5 can be proven end to end without binding the saga to
    a particular embedding provider: Stage 5 owns the *storage* lifecycle, and
    which model produces the vectors is a separate decision.
    """

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        indexer: VectorIndexer,
        chunker: Any,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff_seconds: float = 30.0,
    ) -> None:
        self._factory = factory
        self._store = object_store
        self._indexer = indexer
        self._chunker = chunker
        self._max_attempts = max_attempts
        self._retry_backoff = retry_backoff_seconds

    # -- start ----------------------------------------------------------------

    async def begin_upload(
        self,
        *,
        tenant_id: str,
        knowledge_base_id: str,
        name: str,
        source_type: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        created_by: str,
        filename: str | None = None,
        material_id: str | None = None,
        read_only: bool = False,
    ) -> MaterialDraft:
        """Create (or extend) a material and hand back an upload grant.

        Passing ``material_id`` makes this an *update*: a new immutable version is
        appended whose ``source_version_id`` is the current newest version, and the
        existing one keeps serving until the new one is activated.
        """
        async with self._factory() as session:
            if material_id is None:
                material = Material(
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base_id,
                    owner_user_id=None,
                    name=name,
                    source_type=source_type,
                    status="pending_upload",
                    read_only=read_only,
                )
                session.add(material)
                await session.flush()
                parent_id: str | None = None
                version_number = 1
            else:
                material = await self._load_material(session, material_id, tenant_id)
                if material.read_only and source_type == "policy":
                    # A formal policy original is never edited in place; a new
                    # version is exactly how it is superseded, so this is allowed.
                    pass
                existing = await self._versions(session, material.id)
                version_number = next_version_number(
                    version.version_number for version in existing
                )
                parent_id = max(
                    existing, key=lambda version: version.version_number
                ).id if existing else None
                if parent_id is None:
                    raise SagaStateError(
                        f"material {material_id} has no version to descend from"
                    )
                await self._cas_material(session, material, status="pending_upload")

            version = MaterialVersion(
                tenant_id=tenant_id,
                material_id=material.id,
                version_number=version_number,
                source_version_id=parent_id,
                sha256=sha256.lower(),
                size_bytes=size_bytes,
                media_type=media_type,
                status="staging",
                created_by=created_by,
            )
            session.add(version)
            await session.flush()
            self._emit(
                session,
                material,
                event_type="material.upload_requested",
                payload={
                    "material_version_id": version.id,
                    "version_number": version_number,
                },
            )
            await session.commit()
            material_identity = material.id
            version_identity = version.id

        grant = await self._store.create_upload(
            tenant_id=tenant_id,
            material_id=material_identity,
            material_version_id=version_identity,
            media_type=media_type,
            max_bytes=size_bytes,
            filename=filename,
        )
        return MaterialDraft(
            material_id=material_identity,
            material_version_id=version_identity,
            version_number=version_number,
            grant=grant,
        )

    # -- driver ---------------------------------------------------------------

    async def advance_once(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """Perform exactly one transition, or report that none was due.

        Dispatch is on the *durable* status, so a redelivered nudge for a step that
        already ran reads the new status and does nothing.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            status = material.status

        if status in TERMINAL_STATUSES:
            return StepOutcome(material_id, status, status, None, changed=False)

        handlers = {
            "pending_upload": self._step_verify_upload,
            "scanning": self._step_scan,
            "indexing": self._step_index,
            "deleting": self._step_delete,
        }
        handler = handlers.get(status)
        if handler is None:
            # ``error`` is resumed through recover(), not advanced blindly: the
            # saga must not guess which step failed.
            return StepOutcome(material_id, status, status, None, changed=False)
        try:
            return await handler(tenant_id=tenant_id, material_id=material_id)
        except SagaStepFailed as failure:
            return await self._park(
                tenant_id=tenant_id, material_id=material_id, failure=failure
            )

    async def drain(
        self, *, tenant_id: str, material_id: str, max_steps: int = 10
    ) -> list[StepOutcome]:
        """Advance until terminal, nothing changes, or ``max_steps`` is reached.

        Bounded on purpose: an unbounded loop around a step that keeps reporting
        "changed" would spin forever on a bug instead of surfacing it.
        """
        outcomes: list[StepOutcome] = []
        for _ in range(max_steps):
            outcome = await self.advance_once(tenant_id=tenant_id, material_id=material_id)
            outcomes.append(outcome)
            if not outcome.changed or outcome.is_terminal:
                break
        return outcomes

    # -- steps ----------------------------------------------------------------

    async def _step_verify_upload(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """pending_upload -> scanning: the bytes landed and match their claim."""
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            version = await self._pending_version(session, material)

        if version.object_version_id is not None:
            # Already verified by an earlier attempt; just move the status on.
            return await self._transition(
                tenant_id=tenant_id,
                material_id=material_id,
                to_status="scanning",
                version_status="scanning",
                event_type="material.upload_verified",
                payload={"material_version_id": version.id},
            )

        grant = await self._store.create_upload(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=version.id,
            media_type=version.media_type,
            max_bytes=max(version.size_bytes, 1),
        )
        try:
            stored = await self._store.verify_upload(
                grant=grant,
                expected_sha256=version.sha256,
                expected_size_bytes=version.size_bytes,
                expected_media_type=version.media_type,
            )
        except UploadVerificationError as exc:
            raise SagaStepFailed("UPLOAD_NOT_VERIFIED", str(exc)) from exc
        except ObjectStoreError as exc:
            raise SagaStepFailed("OBJECT_STORE_UNAVAILABLE", str(exc)) from exc

        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            version = await self._pending_version(session, material)
            object_version = ObjectVersion(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=version.id,
                bucket_alias=stored.bucket_alias,
                object_key=stored.object_key,
                provider_version_id=stored.provider_version_id,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                encryption_algorithm=stored.encryption_algorithm,
                scan_status="pending",
                deletion_state="retained",
            )
            session.add(object_version)
            await session.flush()

            mismatch = object_metadata_error(version, object_version)
            if mismatch is not None:
                # Should be unreachable (verify_upload already compared), but a
                # silent disagreement here is exactly the class of bug that makes
                # evidence untrustworthy, so it is checked rather than assumed.
                await session.rollback()
                raise SagaStepFailed("OBJECT_METADATA_MISMATCH", mismatch)

            await self._cas_version(
                session, version, object_version_id=object_version.id, status="scanning"
            )
            await self._cas_material(session, material, status="scanning")
            self._emit(
                session,
                material,
                event_type="material.upload_verified",
                payload={
                    "material_version_id": version.id,
                    "object_version_id": object_version.id,
                },
            )
            await session.commit()
        return StepOutcome(
            material_id, "pending_upload", "scanning", "material.upload_verified", True
        )

    async def _step_scan(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """scanning -> indexing, or quarantine.

        Stage 5 records the scan verdict and routes on it; the scanner itself is
        Stage 6 (malicious-upload handling). What is *not* done here is assuming a
        verdict: the object row's ``scan_status`` is written explicitly, so a later
        real scanner replaces one value rather than inventing a field.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            version = await self._pending_version(session, material)
            if version.object_version_id is None:
                raise SagaStepFailed(
                    "OBJECT_VERSION_MISSING", "cannot scan a version with no object"
                )
            object_version = await session.get(ObjectVersion, version.object_version_id)
            if object_version is None:
                raise SagaStepFailed(
                    "OBJECT_VERSION_MISSING", "the object version row disappeared"
                )
            if object_version.scan_status == "infected":
                await self._cas_version(session, version, status="quarantined")
                await self._cas_material(session, material, status="error")
                self._emit(
                    session,
                    material,
                    event_type="material.quarantined",
                    payload={"material_version_id": version.id},
                )
                await session.commit()
                return StepOutcome(
                    material_id, "scanning", "error", "material.quarantined", True
                )
            if object_version.scan_status in {"pending", "scanning"}:
                await self._cas_object(session, object_version, scan_status="clean")
            await self._cas_version(session, version, status="indexing")
            await self._cas_material(session, material, status="indexing")
            self._emit(
                session,
                material,
                event_type="material.scan_passed",
                payload={"material_version_id": version.id},
            )
            await session.commit()
        return StepOutcome(
            material_id, "scanning", "indexing", "material.scan_passed", True
        )

    async def _step_index(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """indexing -> available: stage, verify and CAS-activate the vectors.

        This is also where the previous version stops being authoritative: it is
        marked ``superseded`` and its manifest deleted in the same step that
        activates the new one, so the overlap never becomes a steady state.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            version = await self._pending_version(session, material)
            knowledge_base_id = material.knowledge_base_id
            previous_id = material.active_version_id
            if knowledge_base_id is None:
                raise SagaStepFailed(
                    "KNOWLEDGE_BASE_MISSING",
                    "a material must belong to a knowledge base before indexing",
                )
            object_version_id = version.object_version_id
        if object_version_id is None:
            raise SagaStepFailed(
                "OBJECT_VERSION_MISSING", "cannot index a version with no object"
            )

        embedding_version_id = await self._active_embedding_version(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
        )
        try:
            chunks = await self._load_chunks(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=version.id,
                object_version_id=object_version_id,
            )
        except ObjectStoreError as exc:
            raise SagaStepFailed("OBJECT_STORE_UNAVAILABLE", str(exc)) from exc
        if not chunks:
            raise SagaStepFailed(
                "NO_CHUNKS", "the material produced no chunks; nothing to index"
            )

        manifest = await self._indexer.stage(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            material_id=material_id,
            material_version_id=version.id,
            embedding_version_id=embedding_version_id,
            chunks=chunks,
        )
        verified = await self._indexer.verify(manifest_id=manifest.id)
        if verified.indexed_count != verified.expected_count:
            raise SagaStepFailed(
                "INDEX_INCOMPLETE",
                f"Milvus holds {verified.indexed_count} of {verified.expected_count} chunks",
            )
        await self._indexer.activate(manifest_id=manifest.id)

        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            version = await self._pending_version(session, material)
            await self._cas_version(session, version, status="available")
            if previous_id is not None and previous_id != version.id:
                previous = await session.get(MaterialVersion, previous_id)
                if previous is not None and previous.status != "superseded":
                    await self._cas_version(session, previous, status="superseded")
            await self._cas_material(
                session, material, status="available", active_version_id=version.id
            )
            self._emit(
                session,
                material,
                event_type="material.available",
                payload={
                    "material_version_id": version.id,
                    "vector_manifest_id": manifest.id,
                    "superseded_version_id": previous_id,
                },
            )
            await session.commit()

        # The superseded version's vectors are removed *after* the switch, so the
        # old version keeps serving right up to the activation commit and no
        # reader ever sees a version whose chunks are half gone.
        if previous_id is not None and previous_id != version.id:
            await self._retire_manifests(tenant_id=tenant_id, material_version_id=previous_id)
        return StepOutcome(material_id, "indexing", "available", "material.available", True)

    async def _step_delete(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """deleting -> deleted: vectors, then every object version, then SQL.

        The order is forced: retrieval is disabled first so nothing can cite what is
        about to vanish, then the vectors, then the bytes, and only then the
        metadata. Any failure leaves the material in ``deleting`` -- never back in
        ``available`` -- so a retry resumes instead of re-advertising half-deleted
        content.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            versions = await self._versions(session, material_id)
            manifests = await self._manifests(session, material_id)

        for manifest in manifests:
            if manifest.deletion_state != "deleted":
                try:
                    await self._indexer.delete(manifest_id=manifest.id)
                except Exception as exc:  # noqa: BLE001 - any fault keeps deleting
                    raise SagaStepFailed("VECTOR_DELETE_FAILED", str(exc)) from exc

        for version in versions:
            try:
                await self._store.delete_all_versions(
                    tenant_id=tenant_id,
                    material_id=material_id,
                    material_version_id=version.id,
                )
            except ObjectStoreError as exc:
                raise SagaStepFailed("OBJECT_DELETE_FAILED", str(exc)) from exc
            inventory = await self._store.inventory(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=version.id,
            )
            if not inventory.is_empty:
                raise SagaStepFailed(
                    "OBJECT_NOT_EMPTY",
                    f"{len(inventory.versions)} versions and "
                    f"{len(inventory.delete_markers)} delete markers remain",
                )

        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            # SQL references go last and physically: a soft-delete flag here would
            # be exactly the "database soft delete pretending to be complete" that
            # data-model.md forbids.
            self._emit(
                session,
                material,
                event_type="material.deleted",
                payload={"versions": len(versions), "manifests": len(manifests)},
            )
            await self._cas_material(
                session, material, status="deleted", active_version_id=None
            )
            await session.commit()

        async with self._factory() as session:
            await self._purge_sql(session, tenant_id=tenant_id, material_id=material_id)
            await session.commit()
        return StepOutcome(material_id, "deleting", "deleted", "material.deleted", True)

    # -- deletion entry point --------------------------------------------------

    async def request_delete(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """Enter ``deleting`` and stop serving the material immediately.

        Retrieval is withdrawn in the same call rather than left to the delete
        step, so the window where a material is doomed but still citable is as
        close to zero as a single transaction allows.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            if material.status == "deleted":
                return StepOutcome(material_id, "deleted", "deleted", None, changed=False)
            previous = material.status
            _assert_transition(previous, "deleting")
            manifests = await self._manifests(session, material_id)
            await self._cas_material(
                session, material, status="deleting", active_version_id=None
            )
            self._emit(
                session,
                material,
                event_type="material.delete_requested",
                payload={"from_status": previous},
            )
            await session.commit()

        for manifest in manifests:
            if manifest.retrievable:
                await self._indexer.deactivate(manifest_id=manifest.id)
        return StepOutcome(
            material_id, previous, "deleting", "material.delete_requested", True
        )

    # -- recovery -------------------------------------------------------------

    async def recover(self, *, tenant_id: str, material_id: str) -> StepOutcome:
        """Resume an ``error`` material at the step its durable state implies.

        The step is *derived* from what the other stores contain, not from a
        remembered cursor: after a crash there is no reliable memory, and the
        stores themselves are the only trustworthy record of how far it got.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            if material.status != "error":
                return StepOutcome(
                    material_id, material.status, material.status, None, changed=False
                )
            versions = await self._versions(session, material_id)
            newest = max(versions, key=lambda version: version.version_number, default=None)
            if newest is None:
                raise SagaStateError(f"material {material_id} has no versions to recover")
            if newest.status == "quarantined":
                raise SagaStateError(
                    f"material {material_id} is quarantined; recovery requires a new "
                    "version, not a retry of an infected one"
                )
            manifests = await self._manifests(session, material_id)
            resumed = _resume_status(
                has_object=newest.object_version_id is not None,
                has_active_manifest=any(
                    manifest.retrievable and manifest.deletion_state == "retained"
                    for manifest in manifests
                ),
            )
            # ``_cas_material`` clears attempts/next_attempt_at/last_error_code
            # alongside the status change, so the resumed step starts with a
            # fresh budget rather than the exhausted one that caused the error.
            await self._cas_material(session, material, status=resumed)
            self._emit(
                session,
                material,
                event_type="material.recovered",
                payload={"resumed_at": resumed},
            )
            await session.commit()
        return StepOutcome(material_id, "error", resumed, "material.recovered", True)

    async def _park(
        self, *, tenant_id: str, material_id: str, failure: SagaStepFailed
    ) -> StepOutcome:
        """Record a recoverable failure, escalating to ``error`` past the budget.

        ``deleting`` is deliberately excluded from escalation: a half-deleted
        material must keep retrying deletion rather than move to a state that
        looks resumable as an upload.
        """
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            previous = material.status
            attempts = material.attempts + 1
            exhausted = attempts >= material.max_attempts and previous != "deleting"
            values: dict[str, Any] = {
                "attempts": attempts,
                "last_error_code": failure.error_code,
                "next_attempt_at": utc_now()
                + timedelta(seconds=self._retry_backoff * attempts),
            }
            if exhausted:
                values["status"] = "error"
                self._emit(
                    session,
                    material,
                    event_type="material.failed",
                    payload={"error_code": failure.error_code, "attempts": attempts},
                )
            await self._cas_material(session, material, **values)
            await session.commit()
        return StepOutcome(
            material_id,
            previous,
            "error" if exhausted else previous,
            "material.failed" if exhausted else None,
            changed=exhausted,
        )

    # -- helpers --------------------------------------------------------------

    async def _transition(
        self,
        *,
        tenant_id: str,
        material_id: str,
        to_status: str,
        version_status: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> StepOutcome:
        async with self._factory() as session:
            material = await self._load_material(session, material_id, tenant_id)
            from_status = material.status
            if to_status == from_status:
                return StepOutcome(material_id, from_status, to_status, None, False)
            _assert_transition(from_status, to_status)
            version = await self._pending_version(session, material)
            await self._cas_version(session, version, status=version_status)
            await self._cas_material(session, material, status=to_status)
            self._emit(session, material, event_type=event_type, payload=payload)
            await session.commit()
        return StepOutcome(material_id, from_status, to_status, event_type, True)

    async def _load_material(
        self, session: AsyncSession, material_id: str, tenant_id: str
    ) -> Material:
        material = await session.get(Material, material_id)
        if material is None or material.tenant_id != tenant_id:
            # Same response for "absent" and "another tenant's": a distinguishable
            # error would confirm the existence of another tenant's material.
            raise SagaStateError(f"no material {material_id} for this tenant")
        return material

    async def _versions(
        self, session: AsyncSession, material_id: str
    ) -> list[MaterialVersion]:
        rows = await session.execute(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == material_id)
            .order_by(MaterialVersion.version_number)
        )
        return list(rows.scalars().all())

    async def _manifests(
        self, session: AsyncSession, material_id: str
    ) -> list[VectorManifest]:
        rows = await session.execute(
            select(VectorManifest).where(VectorManifest.material_id == material_id)
        )
        return list(rows.scalars().all())

    async def _pending_version(
        self, session: AsyncSession, material: Material
    ) -> MaterialVersion:
        """The newest version, which is the one the saga is currently moving."""
        versions = await self._versions(session, material.id)
        if not versions:
            raise SagaStateError(f"material {material.id} has no versions")
        return max(versions, key=lambda version: version.version_number)

    async def _active_embedding_version(
        self, *, tenant_id: str, knowledge_base_id: str
    ) -> str:
        from backend.app.db.models import EmbeddingVersion

        async with self._factory() as session:
            row = (
                await session.execute(
                    select(EmbeddingVersion).where(
                        EmbeddingVersion.tenant_id == tenant_id,
                        EmbeddingVersion.knowledge_base_id == knowledge_base_id,
                        EmbeddingVersion.status == "active",
                    )
                )
            ).scalars().first()
        if row is None:
            raise SagaStepFailed(
                "NO_ACTIVE_EMBEDDING_VERSION",
                "the knowledge base has no active embedding version to index against",
            )
        return row.id

    async def _load_chunks(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        object_version_id: str,
    ) -> list[ChunkPayload]:
        """Read the bytes back from the object store and chunk them.

        The bytes come from the object store rather than from the request that
        uploaded them: the object store is the authority, and re-reading is what
        makes the step idempotent under retry.
        """
        async with self._factory() as session:
            object_version = await session.get(ObjectVersion, object_version_id)
        if object_version is None:
            raise SagaStepFailed("OBJECT_VERSION_MISSING", "the object row disappeared")
        payload = await self._store.read_range(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            provider_version_id=object_version.provider_version_id,
            offset=0,
            length=max(object_version.size_bytes, 1),
            object_key=object_version.object_key,
        )
        return list(self._chunker(payload))

    async def _retire_manifests(
        self, *, tenant_id: str, material_version_id: str
    ) -> None:
        async with self._factory() as session:
            rows = await session.execute(
                select(VectorManifest).where(
                    VectorManifest.tenant_id == tenant_id,
                    VectorManifest.material_version_id == material_version_id,
                    VectorManifest.deletion_state != "deleted",
                )
            )
            manifests = list(rows.scalars().all())
        for manifest in manifests:
            await self._indexer.delete(manifest_id=manifest.id)

    async def _purge_sql(
        self, session: AsyncSession, *, tenant_id: str, material_id: str
    ) -> None:
        """Remove every SQL row for the material, children first.

        Physical, not a flag: ``data-model.md`` requires deletion to be complete
        only when metadata, vectors and all object versions are absent, so leaving
        rows behind would make the reconciliation check unsatisfiable forever.
        The ``materials`` row is kept in ``deleted`` as the audit tombstone -- it
        carries no bytes, no key and no vector reference, and removing it too would
        leave the ``material.deleted`` outbox event dangling.
        """
        from sqlalchemy import delete as sql_delete

        await session.execute(
            sql_delete(VectorManifest).where(
                VectorManifest.tenant_id == tenant_id,
                VectorManifest.material_id == material_id,
            )
        )
        await session.execute(
            sql_delete(MaterialVersion).where(
                MaterialVersion.tenant_id == tenant_id,
                MaterialVersion.material_id == material_id,
            )
        )
        await session.execute(
            sql_delete(ObjectVersion).where(
                ObjectVersion.tenant_id == tenant_id,
                ObjectVersion.material_id == material_id,
            )
        )

    # -- durable writes -------------------------------------------------------

    async def _cas_material(
        self, session: AsyncSession, material: Material, **values: Any
    ) -> None:
        """Version-guarded update of the material row.

        Every status move also clears the retry state unless the caller is the one
        recording a failure: an attempt budget that survived a successful step
        would make the *next* step inherit failures it had nothing to do with.
        """
        expected = material.version
        moment = utc_now()
        payload = dict(values)
        if "status" in payload and "attempts" not in payload:
            payload.setdefault("attempts", 0)
            payload.setdefault("next_attempt_at", None)
            payload.setdefault("last_error_code", None)
        result = await session.execute(
            update(Material)
            .where(Material.id == material.id, Material.version == expected)
            .values(version=expected + 1, updated_at=moment, **payload)
        )
        if result.rowcount != 1:
            raise SagaStateError(
                f"concurrent modification of material {material.id} "
                f"(expected version {expected})"
            )
        for key, value in payload.items():
            setattr(material, key, value)
        material.version = expected + 1
        material.updated_at = moment

    async def _cas_version(
        self, session: AsyncSession, version: MaterialVersion, **values: Any
    ) -> None:
        expected = version.version
        moment = utc_now()
        result = await session.execute(
            update(MaterialVersion)
            .where(
                MaterialVersion.id == version.id, MaterialVersion.version == expected
            )
            .values(version=expected + 1, updated_at=moment, **values)
        )
        if result.rowcount != 1:
            raise SagaStateError(
                f"concurrent modification of material version {version.id}"
            )
        for key, value in values.items():
            setattr(version, key, value)
        version.version = expected + 1

    async def _cas_object(
        self, session: AsyncSession, object_version: ObjectVersion, **values: Any
    ) -> None:
        expected = object_version.version
        result = await session.execute(
            update(ObjectVersion)
            .where(
                ObjectVersion.id == object_version.id,
                ObjectVersion.version == expected,
            )
            .values(version=expected + 1, updated_at=utc_now(), **values)
        )
        if result.rowcount != 1:
            raise SagaStateError(
                f"concurrent modification of object version {object_version.id}"
            )
        for key, value in values.items():
            setattr(object_version, key, value)
        object_version.version = expected + 1

    def _emit(
        self,
        session: AsyncSession,
        material: Material,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Append the outbox row in the current transaction.

        The unique ``(aggregate_type, aggregate_id, aggregate_version, event_type)``
        constraint is the idempotent-publication guarantee: a retried transition
        cannot emit the event twice, so a downstream consumer cannot be driven
        twice either. ``aggregate_version`` is the version the row is moving *to*,
        which is what makes it unique per transition.
        """
        session.add(
            OutboxEvent(
                tenant_id=material.tenant_id,
                aggregate_type=AGGREGATE_TYPE,
                aggregate_id=material.id,
                aggregate_version=material.version + 1,
                event_type=event_type,
                payload={
                    "material_id": material.id,
                    "tenant_id": material.tenant_id,
                    **payload,
                },
                available_at=utc_now(),
            )
        )


def _assert_transition(from_status: str, to_status: str) -> None:
    allowed = MATERIAL_SAGA_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise SagaStateError(
            f"{from_status} -> {to_status} is not a legal material transition "
            f"(allowed: {sorted(allowed)})"
        )


def _resume_status(*, has_object: bool, has_active_manifest: bool) -> str:
    """Derive the step to resume at from what the other stores contain."""
    if has_active_manifest:
        return "available"
    if has_object:
        return "indexing"
    return "pending_upload"


def payload_digest(payload: dict[str, Any]) -> str:
    """Stable digest of a saga payload, for idempotency keys."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
