"""T087 [US1] backfill local document files into versioned object storage.

``data-model.md`` migration steps 5 and 6: copy local files to versioned object
storage and compare byte count *and* SHA-256 before assigning an ``ObjectVersion``;
build Milvus manifests for immutable versions and compare expected chunks and
tenant filters before switching active retrieval.

The ordering here is the whole safety argument, so it is spelled out:

1. **Read and hash the local file.** The on-disk bytes are the source of truth for
   this step; the ``KnowledgeDocument.content_hash`` recorded years ago is treated
   as a *claim to check*, not a fact. A file that no longer matches its recorded
   hash is reported and skipped -- silently importing it would launder a corrupted
   or tampered file into the new authority.
2. **Upload through a derived grant and verify.** The same
   :meth:`ObjectStore.verify_upload` production uses: byte count, SHA-256 and media
   type are compared against what actually landed. Only then is an ``ObjectVersion``
   row written.
3. **Build the Milvus manifest as NOT retrievable.** The vectors are staged and
   verified, but nothing is served: a half-migrated knowledge base must not start
   answering from a partially-built index.
4. **Switch the authority pointer only after verification passes**, as a
   compare-and-set. Until then the legacy path remains authoritative and readers
   see no change.

Nothing destructive happens: the local file is left in place. Removing it is a
separate Stage 9 step, gated on the ``StorageAuthorityTelemetry`` counter showing
zero legacy use across a release window.

Restartable by construction: a document whose material version already verified is
skipped on a later run, so an interrupted backfill resumes rather than duplicating.

Run with:
    python -m migrations.backfill_materials [--tenant <id>] [--limit N] [--activate]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import (
    KnowledgeBase,
    KnowledgeDocument,
    Material,
    MaterialVersion,
    ObjectVersion,
    utc_now,
)
from backend.app.db.session import build_async_engine
from backend.app.retrieval.indexer import ChunkPayload, VectorIndexer
from backend.app.retrieval.milvus import MilvusVectorStore, MilvusVectorStoreConfig
from backend.app.storage.object_store import (
    ObjectStore,
    ObjectStoreConfig,
    ObjectStoreError,
    UploadVerificationError,
)

#: Reasons a document is skipped. Each one is a decision not to import something,
#: so each is reported individually rather than collapsed into a failure count.
SKIP_MISSING_FILE = "local_file_missing"
SKIP_HASH_MISMATCH = "local_file_hash_mismatch"
SKIP_ALREADY_MIGRATED = "already_migrated"
SKIP_NO_TENANT = "document_has_no_tenant"
SKIP_EMPTY = "local_file_empty"


@dataclass
class DocumentOutcome:
    """What the backfill did with one document."""

    document_id: str
    knowledge_base_id: str
    status: str
    material_id: str | None = None
    material_version_id: str | None = None
    object_version_id: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    expected_chunks: int = 0
    indexed_chunks: int = 0
    retrievable: bool = False
    detail: str | None = None


@dataclass
class BackfillReport:
    """Aggregate result, written to the evidence directory as JSON."""

    scanned: int = 0
    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    activated: int = 0
    outcomes: list[DocumentOutcome] = field(default_factory=list)

    def record(self, outcome: DocumentOutcome) -> None:
        self.outcomes.append(outcome)
        self.scanned += 1
        if outcome.status == "migrated":
            self.migrated += 1
            if outcome.retrievable:
                self.activated += 1
        elif outcome.status == "failed":
            self.failed += 1
        else:
            self.skipped += 1

    def to_json(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "migrated": self.migrated,
            "skipped": self.skipped,
            "failed": self.failed,
            "activated": self.activated,
            "outcomes": [asdict(outcome) for outcome in self.outcomes],
        }


def _chunk_document(content_text: str, *, dimensions: int) -> list[ChunkPayload]:
    """Deterministic chunking for migrated content.

    The migration does *not* call an embedding provider: a backfill that depends
    on a paid external service cannot be restarted freely, and the vectors it
    produced would differ from run to run, which defeats the byte-for-byte
    verification this step exists to provide. The manifest is therefore built as
    not-retrievable with deterministic placeholder vectors, and a real re-embed
    runs afterwards under the normal indexing path, where it is observable and
    rate-limited. This is why ``--activate`` is opt-in and off by default.
    """
    lines = [line.strip() for line in content_text.splitlines() if line.strip()]
    chunks: list[ChunkPayload] = []
    for index, line in enumerate(lines):
        digest = hashlib.sha256(line.encode("utf-8")).digest()
        vector = [digest[position % len(digest)] / 255.0 for position in range(dimensions)]
        chunks.append(ChunkPayload(chunk_id=f"c{index}", text=line, vector=vector))
    return chunks


class MaterialBackfill:
    """Copies legacy local document files into the versioned authority."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        indexer: VectorIndexer,
        dimensions: int,
    ) -> None:
        self._factory = factory
        self._store = object_store
        self._indexer = indexer
        self._dimensions = dimensions

    async def run(
        self,
        *,
        tenant_id: str | None = None,
        limit: int | None = None,
        activate: bool = False,
    ) -> BackfillReport:
        """Migrate documents, newest-last so a partial run is easy to reason about."""
        await self._store.verify_bucket_contract()
        report = BackfillReport()
        async with self._factory() as session:
            statement = (
                select(KnowledgeDocument, KnowledgeBase)
                .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.knowledge_base_id)
                .where(KnowledgeDocument.index_status != "deleted")
                .order_by(KnowledgeDocument.created_at)
            )
            if tenant_id is not None:
                statement = statement.where(KnowledgeDocument.tenant_id == tenant_id)
            if limit is not None:
                statement = statement.limit(limit)
            rows = list((await session.execute(statement)).all())

        for document, knowledge_base in rows:
            try:
                outcome = await self._migrate_one(
                    document=document, knowledge_base=knowledge_base, activate=activate
                )
            except Exception as exc:  # noqa: BLE001 - one bad document must not stop the run
                outcome = DocumentOutcome(
                    document_id=document.id,
                    knowledge_base_id=knowledge_base.id,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            report.record(outcome)
        return report

    async def _migrate_one(
        self, *, document: KnowledgeDocument, knowledge_base: KnowledgeBase, activate: bool
    ) -> DocumentOutcome:
        tenant_id = getattr(document, "tenant_id", None) or getattr(
            knowledge_base, "tenant_id", None
        )
        base = DocumentOutcome(
            document_id=document.id,
            knowledge_base_id=knowledge_base.id,
            status="skipped",
        )
        if not tenant_id:
            base.detail = SKIP_NO_TENANT
            return base

        existing = await self._already_migrated(tenant_id=tenant_id, document=document)
        if existing is not None:
            base.detail = SKIP_ALREADY_MIGRATED
            base.material_id = existing.material_id
            base.material_version_id = existing.id
            base.object_version_id = existing.object_version_id
            base.sha256 = existing.sha256
            base.size_bytes = existing.size_bytes
            return base

        local_path = Path(document.file_path)
        if not local_path.is_file():
            base.detail = SKIP_MISSING_FILE
            return base
        content = local_path.read_bytes()
        if not content:
            base.detail = SKIP_EMPTY
            return base

        actual_sha256 = hashlib.sha256(content).hexdigest()
        if document.content_hash and document.content_hash.lower() != actual_sha256:
            # The recorded hash is a claim; the bytes disagree with it. Importing
            # anyway would launder a corrupted or tampered file into the authority
            # that evidence is cited from.
            base.detail = SKIP_HASH_MISMATCH
            base.sha256 = actual_sha256
            return base

        media_type = _media_type_for(document.file_type)
        material_id, material_version_id = await self._create_version(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base.id,
            document=document,
            sha256=actual_sha256,
            size_bytes=len(content),
            media_type=media_type,
        )

        grant = await self._store.create_upload(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            media_type=media_type,
            max_bytes=len(content),
            filename=local_path.name,
        )
        await self._store.upload_bytes(grant=grant, payload=content)
        try:
            stored = await self._store.verify_upload(
                grant=grant,
                expected_sha256=actual_sha256,
                expected_size_bytes=len(content),
                expected_media_type=media_type,
            )
        except (UploadVerificationError, ObjectStoreError) as exc:
            return DocumentOutcome(
                document_id=document.id,
                knowledge_base_id=knowledge_base.id,
                status="failed",
                material_id=material_id,
                material_version_id=material_version_id,
                detail=f"verification failed: {exc}",
            )

        object_version_id = await self._record_object(
            tenant_id=tenant_id,
            material_id=material_id,
            material_version_id=material_version_id,
            stored=stored,
        )

        chunks = _chunk_document(document.content_text or "", dimensions=self._dimensions)
        manifest = None
        if chunks:
            embedding_version_id = await self._embedding_version(
                tenant_id=tenant_id, knowledge_base_id=knowledge_base.id
            )
            if embedding_version_id is not None:
                manifest = await self._indexer.stage(
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base.id,
                    material_id=material_id,
                    material_version_id=material_version_id,
                    embedding_version_id=embedding_version_id,
                    chunks=chunks,
                )
                manifest = await self._indexer.verify(manifest_id=manifest.id)

        retrievable = False
        if (
            activate
            and manifest is not None
            and manifest.indexed_count == manifest.expected_count
            and manifest.expected_count > 0
        ):
            # The authority pointer flips only now, as a compare-and-set, and only
            # because verification passed. Until this line the legacy path is still
            # the one serving readers.
            activated = await self._indexer.activate(manifest_id=manifest.id)
            retrievable = activated.retrievable
            await self._mark_available(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=material_version_id,
            )

        return DocumentOutcome(
            document_id=document.id,
            knowledge_base_id=knowledge_base.id,
            status="migrated",
            material_id=material_id,
            material_version_id=material_version_id,
            object_version_id=object_version_id,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            expected_chunks=manifest.expected_count if manifest else 0,
            indexed_chunks=manifest.indexed_count if manifest else 0,
            retrievable=retrievable,
        )

    # -- durable writes -------------------------------------------------------

    async def _already_migrated(
        self, *, tenant_id: str, document: KnowledgeDocument
    ) -> MaterialVersion | None:
        """Find a verified version for this document, making the run restartable."""
        async with self._factory() as session:
            material = (
                await session.execute(
                    select(Material).where(
                        Material.tenant_id == tenant_id,
                        Material.name == document.title,
                        Material.knowledge_base_id == document.knowledge_base_id,
                    )
                )
            ).scalars().first()
            if material is None:
                return None
            return (
                await session.execute(
                    select(MaterialVersion)
                    .where(
                        MaterialVersion.material_id == material.id,
                        MaterialVersion.object_version_id.is_not(None),
                    )
                    .order_by(MaterialVersion.version_number.desc())
                )
            ).scalars().first()

    async def _create_version(
        self,
        *,
        tenant_id: str,
        knowledge_base_id: str,
        document: KnowledgeDocument,
        sha256: str,
        size_bytes: int,
        media_type: str,
    ) -> tuple[str, str]:
        async with self._factory() as session:
            material = (
                await session.execute(
                    select(Material).where(
                        Material.tenant_id == tenant_id,
                        Material.name == document.title,
                        Material.knowledge_base_id == knowledge_base_id,
                    )
                )
            ).scalars().first()
            if material is None:
                material = Material(
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base_id,
                    name=document.title,
                    # A migrated knowledge document is a formal policy original, so
                    # it is read-only: edits must create a new version.
                    source_type="policy",
                    status="scanning",
                    read_only=True,
                )
                session.add(material)
                await session.flush()
            existing = list(
                (
                    await session.execute(
                        select(MaterialVersion.version_number).where(
                            MaterialVersion.material_id == material.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            from backend.app.db.models import next_version_number

            number = next_version_number(existing)
            version = MaterialVersion(
                tenant_id=tenant_id,
                material_id=material.id,
                version_number=number,
                source_version_id=(
                    None
                    if number == 1
                    else (
                        await session.execute(
                            select(MaterialVersion.id)
                            .where(MaterialVersion.material_id == material.id)
                            .order_by(MaterialVersion.version_number.desc())
                        )
                    ).scalars().first()
                ),
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                status="staging",
                created_by=document.created_by or "backfill",
            )
            session.add(version)
            await session.commit()
            return material.id, version.id

    async def _record_object(
        self,
        *,
        tenant_id: str,
        material_id: str,
        material_version_id: str,
        stored: Any,
    ) -> str:
        async with self._factory() as session:
            row = ObjectVersion(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=material_version_id,
                bucket_alias=stored.bucket_alias,
                object_key=stored.object_key,
                provider_version_id=stored.provider_version_id,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                encryption_algorithm=stored.encryption_algorithm,
                scan_status=stored.scan_status,
            )
            session.add(row)
            await session.flush()
            version = await session.get(MaterialVersion, material_version_id)
            if version is not None:
                version.object_version_id = row.id
                version.status = "indexing"
                version.updated_at = utc_now()
                version.version += 1
            await session.commit()
            return row.id

    async def _mark_available(
        self, *, tenant_id: str, material_id: str, material_version_id: str
    ) -> None:
        async with self._factory() as session:
            version = await session.get(MaterialVersion, material_version_id)
            material = await session.get(Material, material_id)
            if version is not None and version.tenant_id == tenant_id:
                version.status = "available"
                version.updated_at = utc_now()
                version.version += 1
            if material is not None and material.tenant_id == tenant_id:
                material.status = "available"
                material.active_version_id = material_version_id
                material.updated_at = utc_now()
                material.version += 1
            await session.commit()

    async def _embedding_version(
        self, *, tenant_id: str, knowledge_base_id: str
    ) -> str | None:
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
        return row.id if row is not None else None


#: Legacy ``file_type`` values to media types. Unknown types fall back to
#: ``application/octet-stream`` rather than guessing: a wrong media type would be
#: recorded on an immutable version and compared on every later verification.
_MEDIA_TYPES: dict[str, str] = {
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _media_type_for(file_type: str | None) -> str:
    return _MEDIA_TYPES.get((file_type or "").lower(), "application/octet-stream")


def build_backfill(settings: Settings, *, dimensions: int = 8) -> tuple[MaterialBackfill, Any]:
    """Wire a backfill from application settings. Returns ``(backfill, engine)``."""
    engine = build_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = ObjectStore(ObjectStoreConfig.from_settings(settings))
    vectors = MilvusVectorStore(MilvusVectorStoreConfig.from_settings(settings))
    indexer = VectorIndexer(factory=factory, vector_store=vectors)
    return (
        MaterialBackfill(
            factory=factory,
            object_store=store,
            indexer=indexer,
            dimensions=dimensions,
        ),
        engine,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=None, help="limit to one tenant id")
    parser.add_argument("--limit", type=int, default=None, help="max documents")
    parser.add_argument(
        "--activate",
        action="store_true",
        help=(
            "switch the authority pointer for verified versions. Off by default: "
            "the backfill builds placeholder vectors rather than calling an "
            "embedding provider, so activation is a separate, deliberate decision"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the JSON report here (default: stdout only)",
    )
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    backfill, engine = build_backfill(settings)
    try:
        report = await backfill.run(
            tenant_id=args.tenant, limit=args.limit, activate=args.activate
        )
    finally:
        await engine.dispose()
    payload = report.to_json()
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)
    # A failed document is a non-zero exit so a migration run cannot be mistaken
    # for a clean one in CI or an operator's scrollback.
    return 1 if report.failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main_async(_parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
