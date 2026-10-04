"""T082 [US1] vector indexing: deterministic ids, verified staging, CAS activation.

The lifecycle is deliberately four separate steps rather than one "index it" call,
because each boundary is a point where a crash must leave something recoverable:

1. :meth:`VectorIndexer.stage` writes the vectors with ``retrievable=False`` and
   records (or reuses) a manifest. Nothing is served yet, so a crash here leaves
   dead rows that reconciliation can find by prefix -- never a half-visible
   version.
2. :meth:`VectorIndexer.verify` counts what Milvus actually holds and stores it as
   ``indexed_count``. Activation refuses to proceed unless it equals
   ``expected_count``, so a truncated index cannot be switched in.
3. :meth:`VectorIndexer.activate` is the single instant the answer changes: one
   PostgreSQL transaction demotes the previous manifest and promotes this one
   under compare-and-set. Until it commits, the previous version keeps serving.
4. :meth:`VectorIndexer.delete` removes the vectors and marks the manifest
   deleted, after making it unretrievable first.

Two properties are worth stating explicitly because they are what make the
ordering safe:

* **Deterministic vector ids.** ``vector_id = prefix:chunk_id`` where the prefix
  is derived from (tenant, kb, subject, version, embedding version). Re-staging
  overwrites instead of duplicating, so a retry after a partial failure converges
  rather than double-counting.
* **The retrievable flag is written twice, authority last.** The Milvus projection
  is flipped on *before* the PostgreSQL commit, and the old version's projection
  is flipped off *after*. Since a search requires both the row flag and a version
  id from the authoritative manifest set, every intermediate state is either the
  old version or the new one -- never both, never neither.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import (
    MaterialVersion,
    VectorManifest,
    utc_now,
)
from backend.app.retrieval.milvus import (
    MilvusVectorStore,
    RetrievalScope,
)

#: Namespace for the deterministic vector-id prefix. A constant rather than
#: configuration: changing it would orphan every already-indexed row.
_PREFIX_NAMESPACE = "policyflow/vectors/v1"


class ManifestStateError(RuntimeError):
    """A manifest transition was requested from a state that forbids it."""


@dataclass(frozen=True)
class ChunkPayload:
    """One chunk to index: its stable id, its text and its embedding."""

    chunk_id: str
    text: str
    vector: Sequence[float]


def vector_id_prefix(
    *,
    tenant_id: str,
    knowledge_base_id: str,
    subject_id: str,
    version_id: str,
    embedding_version_id: str,
) -> str:
    """Deterministic id namespace for one (version, embedding version) pair.

    Includes the embedding version so re-embedding the *same* immutable material
    version under a new model produces a disjoint id space: the two cohorts can
    coexist in the collection while only one manifest is retrievable.
    """
    message = "\x1f".join(
        (
            _PREFIX_NAMESPACE,
            tenant_id,
            knowledge_base_id,
            subject_id,
            version_id,
            embedding_version_id,
        )
    ).encode("utf-8")
    return hashlib.sha256(message).hexdigest()[:32]


class VectorIndexer:
    """Owns the staged/verified/activated lifecycle of a vector manifest."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        vector_store: MilvusVectorStore,
    ) -> None:
        self._factory = factory
        self._store = vector_store

    # -- stage ----------------------------------------------------------------

    async def stage(
        self,
        *,
        tenant_id: str,
        knowledge_base_id: str,
        embedding_version_id: str,
        chunks: Sequence[ChunkPayload],
        material_id: str | None = None,
        material_version_id: str | None = None,
        document_id: str | None = None,
    ) -> VectorManifest:
        """Write vectors as not-retrievable and record (or reuse) the manifest.

        Idempotent by construction: the prefix is deterministic, so a retry
        overwrites the same rows and updates the same manifest row rather than
        creating a second one.
        """
        subject_kind, subject_id = _resolve_subject(
            material_version_id=material_version_id, document_id=document_id
        )
        prefix = vector_id_prefix(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            subject_id=subject_id,
            version_id=subject_id,
            embedding_version_id=embedding_version_id,
        )
        content_hash = _content_hash(chunks)

        await self._store.upsert_chunks(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            subject_kind=subject_kind,
            subject_id=material_id or document_id or subject_id,
            version_id=subject_id,
            embedding_version_id=embedding_version_id,
            vector_id_prefix=prefix,
            chunks=chunks,
            retrievable=False,
        )

        async with self._factory() as session:
            existing = await self._find_manifest(
                session,
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                embedding_version_id=embedding_version_id,
                material_version_id=material_version_id,
                document_id=document_id,
            )
            if existing is not None:
                existing.expected_count = len(chunks)
                # A re-stage invalidates the previous count: it must be re-verified
                # against Milvus before it can be activated again.
                existing.indexed_count = 0
                existing.content_hash = content_hash
                existing.chunk_ids = [chunk.chunk_id for chunk in chunks]
                existing.updated_at = utc_now()
                existing.version += 1
                await session.commit()
                await session.refresh(existing)
                return existing

            manifest = VectorManifest(
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                material_id=material_id,
                material_version_id=material_version_id,
                document_id=document_id,
                embedding_version_id=embedding_version_id,
                milvus_database=self._store.config.database,
                milvus_collection=self._store.config.collection,
                vector_id_prefix=prefix,
                chunk_ids=[chunk.chunk_id for chunk in chunks],
                expected_count=len(chunks),
                indexed_count=0,
                content_hash=content_hash,
                retrievable=False,
            )
            session.add(manifest)
            await session.commit()
            await session.refresh(manifest)
            return manifest

    # -- verify ---------------------------------------------------------------

    async def verify(self, *, manifest_id: str) -> VectorManifest:
        """Count what Milvus holds and record it as ``indexed_count``.

        The count comes from Milvus, not from what staging believed it wrote: a
        partially-applied write is exactly the case this step exists to catch.
        """
        async with self._factory() as session:
            manifest = await self._load(session, manifest_id)
            counted = await self._store.count_by_prefix(
                tenant_id=manifest.tenant_id,
                vector_id_prefix=manifest.vector_id_prefix,
            )
            manifest.indexed_count = counted
            manifest.updated_at = utc_now()
            manifest.version += 1
            await session.commit()
            await session.refresh(manifest)
            return manifest

    # -- activate -------------------------------------------------------------

    async def activate(self, *, manifest_id: str) -> VectorManifest:
        """Make this manifest the one retrievable version, under compare-and-set.

        The demote-and-promote happens in one transaction, so there is no instant
        where two versions are authoritative or none is. The update is guarded on
        the row ``version`` (never last-write-wins), so a concurrent activation of
        a third version loses the race instead of interleaving with this one.
        """
        async with self._factory() as session:
            manifest = await self._load(session, manifest_id)
            if manifest.deletion_state != "retained":
                raise ManifestStateError(
                    f"manifest {manifest_id} is {manifest.deletion_state}; a deleted "
                    "manifest cannot be activated"
                )
            if manifest.expected_count <= 0:
                raise ValueError(
                    f"manifest {manifest_id} expects no chunks; nothing to activate"
                )
            if manifest.indexed_count != manifest.expected_count:
                raise ValueError(
                    f"manifest {manifest_id} is not verified: Milvus holds "
                    f"{manifest.indexed_count} of {manifest.expected_count} chunks"
                )
            if manifest.retrievable:
                return manifest

            previous = await self._current_retrievable(session, manifest)

            # Projection first: after this the rows are *eligible*, but the filter
            # also requires a version id from the authoritative set below, so they
            # are still not served.
            await self._store.set_retrievable(
                tenant_id=manifest.tenant_id,
                vector_id_prefix=manifest.vector_id_prefix,
                retrievable=True,
            )

            if previous is not None:
                await _cas(session, previous, retrievable=False)
            await _cas(session, manifest, retrievable=True, activated_at=utc_now())
            await session.commit()

            # Authority has switched; the old projection is now redundant. If this
            # fails the old rows stay flagged in Milvus but are excluded anyway,
            # because their manifest is no longer retrievable -- it fails closed.
            if previous is not None:
                await self._store.set_retrievable(
                    tenant_id=previous.tenant_id,
                    vector_id_prefix=previous.vector_id_prefix,
                    retrievable=False,
                )
            await session.refresh(manifest)
            return manifest

    async def deactivate(self, *, manifest_id: str) -> VectorManifest:
        """Withdraw a manifest from retrieval, authority first.

        The opposite order from activation, and for the same reason: the
        authoritative flag is cleared before the projection, so the rows stop
        being served at the commit rather than at the Milvus round trip.
        """
        async with self._factory() as session:
            manifest = await self._load(session, manifest_id)
            if manifest.retrievable:
                await _cas(session, manifest, retrievable=False)
                await session.commit()
            await self._store.set_retrievable(
                tenant_id=manifest.tenant_id,
                vector_id_prefix=manifest.vector_id_prefix,
                retrievable=False,
            )
            await session.refresh(manifest)
            return manifest

    # -- delete ---------------------------------------------------------------

    async def delete(self, *, manifest_id: str) -> VectorManifest:
        """Physically remove the vectors, then mark the manifest deleted.

        Retrieval is disabled *before* the vectors go, so a reader can never see a
        version whose chunks are half gone. The manifest is only marked ``deleted``
        after Milvus confirms, so a failure leaves it in ``deleting`` for the saga
        to retry (``data-model.md``: physical deletion uses an explicit state
        machine, not a soft-delete flag pretending to be done).
        """
        async with self._factory() as session:
            manifest = await self._load(session, manifest_id)
            if manifest.deletion_state == "deleted":
                return manifest
            if manifest.retrievable or manifest.deletion_state == "retained":
                await _cas(session, manifest, retrievable=False, deletion_state="deleting")
                await session.commit()
                await session.refresh(manifest)

        await self._store.delete_by_prefix(
            tenant_id=manifest.tenant_id, vector_id_prefix=manifest.vector_id_prefix
        )
        remaining = await self._store.count_by_prefix(
            tenant_id=manifest.tenant_id, vector_id_prefix=manifest.vector_id_prefix
        )
        if remaining:
            raise ManifestStateError(
                f"{remaining} vectors remain for manifest {manifest_id}; it stays in "
                "deleting so the sweep can retry"
            )

        async with self._factory() as session:
            current = await self._load(session, manifest_id)
            await _cas(
                session,
                current,
                deletion_state="deleted",
                deleted_at=utc_now(),
                indexed_count=0,
            )
            await session.commit()
            await session.refresh(current)
            return current

    # -- scope resolution -----------------------------------------------------

    async def resolve_scope(
        self,
        *,
        tenant_id: str,
        knowledge_base_ids: Sequence[str],
        embedding_version_id: str,
    ) -> RetrievalScope:
        """Build the pre-ANN scope from the authoritative manifest set.

        This is where ``retrievable=true`` enters the filter as an authority
        decision rather than a cached flag: only versions whose manifest is
        currently retrievable, undeleted and on this embedding version are listed.
        A caller cannot widen the result, because it never names versions itself.
        """
        async with self._factory() as session:
            rows = await session.execute(
                select(VectorManifest).where(
                    VectorManifest.tenant_id == tenant_id,
                    VectorManifest.knowledge_base_id.in_(list(knowledge_base_ids)),
                    VectorManifest.embedding_version_id == embedding_version_id,
                    VectorManifest.retrievable.is_(True),
                    VectorManifest.deletion_state == "retained",
                )
            )
            manifests = list(rows.scalars().all())
        version_ids = tuple(
            sorted(
                {
                    manifest.material_version_id or manifest.document_id or ""
                    for manifest in manifests
                }
                - {""}
            )
        )
        return RetrievalScope(
            tenant_id=tenant_id,
            knowledge_base_ids=tuple(knowledge_base_ids),
            embedding_version_id=embedding_version_id,
            version_ids=version_ids,
        )

    async def active_material_versions(
        self, *, tenant_id: str, knowledge_base_ids: Sequence[str]
    ) -> tuple[MaterialVersion, ...]:
        """Material versions currently served, for evidence and audit records."""
        async with self._factory() as session:
            rows = await session.execute(
                select(MaterialVersion)
                .join(
                    VectorManifest,
                    VectorManifest.material_version_id == MaterialVersion.id,
                )
                .where(
                    VectorManifest.tenant_id == tenant_id,
                    VectorManifest.knowledge_base_id.in_(list(knowledge_base_ids)),
                    VectorManifest.retrievable.is_(True),
                    VectorManifest.deletion_state == "retained",
                )
            )
            return tuple(rows.scalars().all())

    # -- internals ------------------------------------------------------------

    async def _load(self, session: AsyncSession, manifest_id: str) -> VectorManifest:
        manifest = await session.get(VectorManifest, manifest_id)
        if manifest is None:
            raise ManifestStateError(f"no vector manifest {manifest_id}")
        return manifest

    async def _find_manifest(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        knowledge_base_id: str,
        embedding_version_id: str,
        material_version_id: str | None,
        document_id: str | None,
    ) -> VectorManifest | None:
        statement = select(VectorManifest).where(
            VectorManifest.tenant_id == tenant_id,
            VectorManifest.knowledge_base_id == knowledge_base_id,
            VectorManifest.embedding_version_id == embedding_version_id,
        )
        if material_version_id is not None:
            statement = statement.where(
                VectorManifest.material_version_id == material_version_id
            )
        else:
            statement = statement.where(VectorManifest.document_id == document_id)
        return (await session.execute(statement)).scalars().first()

    async def _current_retrievable(
        self, session: AsyncSession, manifest: VectorManifest
    ) -> VectorManifest | None:
        """The manifest currently serving this subject, if any.

        Scoped by material (or by document), not by version: the point of the
        lookup is to find the row that must be demoted so the partial unique index
        is never violated.
        """
        statement = select(VectorManifest).where(
            VectorManifest.tenant_id == manifest.tenant_id,
            VectorManifest.knowledge_base_id == manifest.knowledge_base_id,
            VectorManifest.retrievable.is_(True),
            VectorManifest.id != manifest.id,
        )
        if manifest.material_id is not None:
            statement = statement.where(VectorManifest.material_id == manifest.material_id)
        elif manifest.document_id is not None:
            statement = statement.where(VectorManifest.document_id == manifest.document_id)
        else:  # pragma: no cover - the subject CHECK forbids this
            return None
        return (await session.execute(statement)).scalars().first()


async def _cas(
    session: AsyncSession, manifest: VectorManifest, **values: object
) -> None:
    """Apply a version-guarded update of ``manifest`` in the open transaction.

    The guard is what makes concurrent activation safe: two activations of
    different versions cannot both succeed, because the second one's
    ``WHERE version = n`` no longer matches and the row count comes back zero.
    The in-memory object is updated to match so the caller sees the new state
    without a refresh.
    """
    expected = manifest.version
    moment = utc_now()
    result = await session.execute(
        update(VectorManifest)
        .where(VectorManifest.id == manifest.id, VectorManifest.version == expected)
        .values(version=expected + 1, updated_at=moment, **values)
    )
    if result.rowcount != 1:
        raise ManifestStateError(
            f"concurrent modification of manifest {manifest.id} "
            f"(expected version {expected})"
        )
    for key, value in values.items():
        setattr(manifest, key, value)
    manifest.version = expected + 1
    manifest.updated_at = moment


def _resolve_subject(
    *, material_version_id: str | None, document_id: str | None
) -> tuple[str, str]:
    """Return ``(subject_kind, version_id)``, enforcing exactly one subject."""
    if bool(material_version_id) == bool(document_id):
        raise ValueError(
            "a vector manifest describes exactly one subject: either a material "
            "version or a knowledge document"
        )
    if material_version_id:
        return "material", material_version_id
    assert document_id is not None
    return "document", document_id


def _content_hash(chunks: Sequence[ChunkPayload]) -> str:
    """Stable hash of the indexed content, for drift detection."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.chunk_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(chunk.text.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()
