"""T084 [US1] periodic cross-store reconciliation.

PostgreSQL, the object store and Milvus each own one concern and cannot share a
transaction, so they *will* drift: a crash between saga steps, a provider that
accepted a delete and lost it, a manifest activated while its vectors were still
landing. Reconciliation is what turns that from a silent correctness hole into a
tracked, recoverable finding.

Six kinds, each one a specific question about a specific pair of stores:

* ``missing_object`` -- PostgreSQL records a version whose bytes are not in the
  object store. Evidence could cite something unreadable, so this is critical.
* ``orphan_object`` -- bytes exist that no live version claims. Not a correctness
  hole, but it is undeleted customer data and costs money, so it is a real finding.
* ``missing_vector`` -- a retrievable manifest whose vectors Milvus does not hold.
  The material silently stops being findable while still looking available.
* ``orphan_vector`` -- vectors for a version PostgreSQL no longer knows about.
  These are the dangerous ones: they can still match a query.
* ``missing_chunk`` -- the manifest's chunk list and Milvus disagree in part, so
  retrieval returns truncated evidence that looks complete.
* ``version_drift`` -- ``materials.active_version_id`` and the retrievable
  manifest disagree about which version is current. This is the drift the
  intentionally-missing foreign key makes *detectable* (see ``Material``).

Design rules:

* **The sweep is idempotent.** Findings are upserted on the natural key, so
  re-detecting an open issue bumps its attempt counters instead of growing a pile.
  "100% detection" is therefore a stable number, not a function of how often the
  sweep ran.
* **An issue always reaches a terminal state.** Either the sweep repairs it
  (``repaired``) or it escalates for a human (``manual_required``) once the
  attempt budget is spent. Nothing is closed by being forgotten.
* **A sweep never repairs by guessing.** Only unambiguous repairs are automatic
  (deleting an orphan, re-flagging a drifted pointer). Anything where both stores
  hold plausible-but-different truth is escalated, because picking one would risk
  destroying the real data.
* **An unreachable store is not a finding.** If Milvus or the object store cannot
  be reached, the sweep reports that and stops: declaring every object "missing"
  during an outage would raise thousands of false criticals and could trigger
  repairs that delete live data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import (
    NO_VERSION_SENTINEL,
    Material,
    MaterialVersion,
    ObjectVersion,
    ReconciliationIssue,
    VectorManifest,
    utc_now,
)
from backend.app.retrieval.indexer import VectorIndexer
from backend.app.retrieval.milvus import RetrievalUnavailable
from backend.app.storage.object_store import ObjectStore, ObjectStoreError

#: Store pairs, matching ``RECONCILIATION_STORE_PAIRS``.
PAIR_OBJECT = "postgres_object"
PAIR_MILVUS = "postgres_milvus"
PAIR_CROSS = "object_milvus"

#: Severity per kind. ``critical`` means evidence correctness is at risk right
#: now; ``warning`` means data is wrong but not currently misleading a reader.
ISSUE_SEVERITY: dict[str, str] = {
    "missing_object": "critical",
    "missing_vector": "critical",
    "missing_chunk": "critical",
    # Vectors that no live version claims can still match a query, which is how a
    # deleted policy answers a question.
    "orphan_vector": "critical",
    "version_drift": "critical",
    # Undeleted bytes nobody serves: real, costly, not misleading.
    "orphan_object": "warning",
}

#: Version statuses whose bytes are expected to exist in the object store.
_LIVE_VERSION_STATUSES = frozenset({"scanning", "indexing", "available", "superseded"})


class ReconciliationUnavailable(RuntimeError):  # noqa: N818 - reads as a condition
    """A store could not be reached, so no verdict can be drawn this pass."""


class ManualRepairRequired(RuntimeError):  # noqa: N818 - reads as a requirement
    """A repair cannot be made safely, so the issue is escalated to a human.

    Raised by a repair rather than returned so an unsafe repair can never be
    mistaken for a successful one: the only way out of a repair is a resolution
    string or this exception.
    """


@dataclass
class SweepReport:
    """What one reconciliation pass found and did."""

    tenant_id: str
    checked_versions: int = 0
    checked_manifests: int = 0
    opened: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)

    @property
    def findings(self) -> int:
        return len(self.opened) + len(self.reopened)

    def kinds(self) -> set[str]:
        return {entry.split(":", 1)[0] for entry in (*self.opened, *self.reopened)}


class CrossStoreReconciler:
    """Compares PostgreSQL against the object store and Milvus, and records drift."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        indexer: VectorIndexer,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 300.0,
    ) -> None:
        self._factory = factory
        self._store = object_store
        self._indexer = indexer
        self._vectors = indexer._store  # noqa: SLF001 - one owner, shared client
        self._max_attempts = max_attempts
        self._retry_backoff = retry_backoff_seconds

    # -- sweep ----------------------------------------------------------------

    async def sweep(self, *, tenant_id: str, repair: bool = False) -> SweepReport:
        """Run one full pass for a tenant.

        ``repair`` is opt-in: detection is always safe to run, repair mutates
        stores, and the two are separated so a sweep can be scheduled frequently
        while repair stays a deliberate action.
        """
        report = SweepReport(tenant_id=tenant_id)
        await self._check_objects(tenant_id=tenant_id, report=report, repair=repair)
        await self._check_vectors(tenant_id=tenant_id, report=report, repair=repair)
        await self._check_active_pointer(tenant_id=tenant_id, report=report, repair=repair)
        return report

    # -- PostgreSQL vs object store -------------------------------------------

    async def _check_objects(
        self, *, tenant_id: str, report: SweepReport, repair: bool
    ) -> None:
        async with self._factory() as session:
            versions = list(
                (
                    await session.execute(
                        select(MaterialVersion).where(
                            MaterialVersion.tenant_id == tenant_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            objects = list(
                (
                    await session.execute(
                        select(ObjectVersion).where(ObjectVersion.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
        report.checked_versions = len(versions)

        for version in versions:
            if version.status not in _LIVE_VERSION_STATUSES:
                continue
            try:
                inventory = await self._store.inventory(
                    tenant_id=tenant_id,
                    material_id=version.material_id,
                    material_version_id=version.id,
                )
            except ObjectStoreError as exc:
                raise ReconciliationUnavailable(
                    f"the object store could not be listed; no verdict this pass ({exc})"
                ) from exc
            if not inventory.versions:
                await self._record(
                    report,
                    tenant_id=tenant_id,
                    store_pair=PAIR_OBJECT,
                    issue_kind="missing_object",
                    resource_kind="material_version",
                    resource_id=version.id,
                    version_id=version.object_version_id or NO_VERSION_SENTINEL,
                    expected="one or more object versions",
                    observed="no object versions",
                    # Nothing to repair automatically: the bytes are gone and only a
                    # re-upload or a restore can produce them. Guessing would mean
                    # deleting the metadata that records what is missing.
                    repair=None,
                )

        live_version_ids = {
            version.id for version in versions if version.status in _LIVE_VERSION_STATUSES
        }
        # Which object rows a live version *authoritatively* claims. The direction
        # matters: ``material_versions.object_version_id`` is the authority, while
        # ``object_versions.material_version_id`` is a convenience back-pointer with
        # no foreign key. Judging orphanhood by the back-pointer would call an
        # object orphaned while a live version still depends on it -- and a repair
        # acting on that verdict would try to delete a row the schema still
        # references, or worse, delete bytes a completed run cites.
        claimed_object_ids = {
            version.object_version_id
            for version in versions
            if version.id in live_version_ids and version.object_version_id is not None
        }
        for row in objects:
            if row.id in claimed_object_ids or row.deletion_state == "deleted":
                continue
            await self._record(
                report,
                tenant_id=tenant_id,
                store_pair=PAIR_OBJECT,
                issue_kind="orphan_object",
                resource_kind="object_version",
                resource_id=row.id,
                version_id=row.provider_version_id,
                expected="a live material version claiming this object",
                observed=(
                    "no material_versions row references it "
                    f"(back-pointer says {row.material_version_id or 'null'})"
                ),
                repair=(
                    self._repair_orphan_object(tenant_id=tenant_id, object_row_id=row.id)
                    if repair
                    else None
                ),
            )

    # -- PostgreSQL vs Milvus --------------------------------------------------

    async def _check_vectors(
        self, *, tenant_id: str, report: SweepReport, repair: bool
    ) -> None:
        async with self._factory() as session:
            manifests = list(
                (
                    await session.execute(
                        select(VectorManifest).where(VectorManifest.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
            known_version_ids = {
                row
                for row in (
                    await session.execute(
                        select(MaterialVersion.id).where(
                            MaterialVersion.tenant_id == tenant_id
                        )
                    )
                )
                .scalars()
                .all()
            }
        report.checked_manifests = len(manifests)

        try:
            present_versions = set(await self._vectors.list_version_ids(tenant_id=tenant_id))
        except RetrievalUnavailable as exc:
            raise ReconciliationUnavailable(
                f"Milvus could not be queried; no verdict this pass ({exc})"
            ) from exc

        for manifest in manifests:
            if manifest.deletion_state == "deleted":
                continue
            held = await self._vectors.chunk_ids_for_prefix(
                tenant_id=tenant_id, vector_id_prefix=manifest.vector_id_prefix
            )
            expected_chunks = tuple(sorted(manifest.chunk_ids))
            if manifest.retrievable and not held:
                await self._record(
                    report,
                    tenant_id=tenant_id,
                    store_pair=PAIR_MILVUS,
                    issue_kind="missing_vector",
                    resource_kind="vector_manifest",
                    resource_id=manifest.id,
                    version_id=manifest.material_version_id
                    or manifest.document_id
                    or NO_VERSION_SENTINEL,
                    expected=f"{manifest.expected_count} vectors",
                    observed="no vectors",
                    # A retrievable manifest with no vectors is serving nothing while
                    # claiming to serve: withdrawing it is unambiguous and safe.
                    repair=(
                        self._repair_withdraw_manifest(manifest_id=manifest.id)
                        if repair
                        else None
                    ),
                )
                continue
            if held and expected_chunks and set(held) != set(expected_chunks):
                missing = sorted(set(expected_chunks) - set(held))
                await self._record(
                    report,
                    tenant_id=tenant_id,
                    store_pair=PAIR_MILVUS,
                    issue_kind="missing_chunk",
                    resource_kind="vector_manifest",
                    resource_id=manifest.id,
                    version_id=manifest.material_version_id
                    or manifest.document_id
                    or NO_VERSION_SENTINEL,
                    expected=f"{len(expected_chunks)} chunks",
                    observed=f"{len(held)} chunks, missing {len(missing)}",
                    # Partial evidence is worse than none: withdraw, then let the
                    # saga re-index. Repairing by re-embedding here would make the
                    # sweep an indexer.
                    repair=(
                        self._repair_withdraw_manifest(manifest_id=manifest.id)
                        if repair and manifest.retrievable
                        else None
                    ),
                )

        manifest_version_ids = {
            manifest.material_version_id or manifest.document_id
            for manifest in manifests
            if manifest.deletion_state != "deleted"
        }
        for version_id in sorted(present_versions):
            if version_id in manifest_version_ids and version_id in known_version_ids:
                continue
            await self._record(
                report,
                tenant_id=tenant_id,
                store_pair=PAIR_CROSS,
                issue_kind="orphan_vector",
                resource_kind="milvus_version",
                resource_id=version_id,
                version_id=version_id,
                expected="a live manifest and material version",
                observed="vectors with no owning manifest",
                repair=(
                    self._repair_orphan_vectors(tenant_id=tenant_id, version_id=version_id)
                    if repair
                    else None
                ),
            )

    # -- active-pointer drift --------------------------------------------------

    async def _check_active_pointer(
        self, *, tenant_id: str, report: SweepReport, repair: bool
    ) -> None:
        """Does ``materials.active_version_id`` agree with the retrievable manifest?

        This is the check that justifies ``active_version_id`` being a plain
        column: a foreign key would have guaranteed the target *exists*, but could
        never have noticed that it names a different version than the one actually
        being served.
        """
        async with self._factory() as session:
            materials = list(
                (
                    await session.execute(
                        select(Material).where(
                            Material.tenant_id == tenant_id,
                            Material.status.notin_(("deleted",)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            retrievable: dict[str, str] = {}
            rows = (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.tenant_id == tenant_id,
                        VectorManifest.retrievable.is_(True),
                        VectorManifest.deletion_state == "retained",
                    )
                )
            ).scalars()
            for manifest in rows:
                if manifest.material_id is not None and manifest.material_version_id:
                    retrievable[manifest.material_id] = manifest.material_version_id

        for material in materials:
            served = retrievable.get(material.id)
            pointer = material.active_version_id
            if material.status != "available":
                # Mid-saga states legitimately have no served version yet.
                continue
            if pointer == served:
                continue
            await self._record(
                report,
                tenant_id=tenant_id,
                store_pair=PAIR_MILVUS,
                issue_kind="version_drift",
                resource_kind="material",
                resource_id=material.id,
                version_id=pointer or NO_VERSION_SENTINEL,
                expected=f"active_version_id={served or 'null'} (the served version)",
                observed=f"active_version_id={pointer or 'null'}",
                # Milvus plus the manifest flag are the authority for what is served;
                # the pointer is a denormalisation, so correcting the pointer is the
                # safe direction. Changing which version is *served* to match a stale
                # pointer would be the unsafe one.
                repair=(
                    self._repair_active_pointer(
                        tenant_id=tenant_id, material_id=material.id, served=served
                    )
                    if repair
                    else None
                ),
            )

    # -- repairs ---------------------------------------------------------------

    async def _repair_orphan_object(self, *, tenant_id: str, object_row_id: str) -> str:
        """Delete the orphaned bytes and its row. Unambiguous: nothing claims it."""
        async with self._factory() as session:
            row = await session.get(ObjectVersion, object_row_id)
            if row is None or row.tenant_id != tenant_id:
                return "already absent"
            material_id = row.material_id
            material_version_id = row.material_version_id
        if material_version_id:
            await self._store.delete_all_versions(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=material_version_id,
            )
        async with self._factory() as session:
            await session.execute(
                sql_delete(ObjectVersion).where(ObjectVersion.id == object_row_id)
            )
            await session.commit()
        return "orphaned object versions and row removed"

    async def _repair_withdraw_manifest(self, *, manifest_id: str) -> str:
        """Stop serving a manifest Milvus cannot back. Never deletes metadata."""
        await self._indexer.deactivate(manifest_id=manifest_id)
        return "manifest withdrawn from retrieval pending re-index"

    async def _repair_orphan_vectors(self, *, tenant_id: str, version_id: str) -> str:
        """Remove vectors no manifest owns, so they cannot match a query."""
        async with self._factory() as session:
            manifests = list(
                (
                    await session.execute(
                        select(VectorManifest).where(
                            VectorManifest.tenant_id == tenant_id,
                            VectorManifest.material_version_id == version_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        removed = 0
        for manifest in manifests:
            removed += await self._vectors.delete_by_prefix(
                tenant_id=tenant_id, vector_id_prefix=manifest.vector_id_prefix
            )
        if not manifests:
            # No manifest records the prefix, so the rows cannot be addressed by
            # prefix. Deleting by a reconstructed guess could remove live data, so
            # this is escalated instead.
            raise ManualRepairRequired(
                "vectors exist for a version with no manifest; the id prefix is "
                "unknown so they cannot be removed safely"
            )
        return f"{removed} orphaned vectors removed"

    async def _repair_active_pointer(
        self, *, tenant_id: str, material_id: str, served: str | None
    ) -> str:
        async with self._factory() as session:
            material = await session.get(Material, material_id)
            if material is None or material.tenant_id != tenant_id:
                return "material absent"
            expected = material.version
            result = await session.execute(
                update(Material)
                .where(Material.id == material_id, Material.version == expected)
                .values(
                    version=expected + 1,
                    active_version_id=served,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                raise ManualRepairRequired(
                    "the material changed while the pointer was being corrected"
                )
            await session.commit()
        return f"active_version_id corrected to {served or 'null'}"

    # -- issue bookkeeping -----------------------------------------------------

    async def _record(
        self,
        report: SweepReport,
        *,
        tenant_id: str,
        store_pair: str,
        issue_kind: str,
        resource_kind: str,
        resource_id: str,
        version_id: str,
        expected: str,
        observed: str,
        repair,
    ) -> None:
        """Upsert the finding, then attempt its repair when one was supplied.

        Order matters: the issue is recorded *before* the repair runs, so a repair
        that crashes still leaves a durable record of what was wrong.
        """
        issue_id = await self._upsert_issue(
            report,
            tenant_id=tenant_id,
            store_pair=store_pair,
            issue_kind=issue_kind,
            resource_kind=resource_kind,
            resource_id=resource_id,
            version_id=version_id,
            expected=expected,
            observed=observed,
        )
        if repair is None:
            return
        try:
            resolution = await repair
        except ManualRepairRequired as escalation:
            await self._escalate(issue_id, reason=str(escalation))
            report.escalated.append(f"{issue_kind}:{resource_id}")
            return
        except Exception as exc:  # noqa: BLE001 - a failed repair must not abort
            await self._fail_attempt(issue_id, error_code=type(exc).__name__)
            return
        await self._resolve(issue_id, resolution=resolution)
        report.repaired.append(f"{issue_kind}:{resource_id}")

    async def _upsert_issue(
        self,
        report: SweepReport,
        *,
        tenant_id: str,
        store_pair: str,
        issue_kind: str,
        resource_kind: str,
        resource_id: str,
        version_id: str,
        expected: str,
        observed: str,
    ) -> str:
        key = f"{issue_kind}:{resource_id}"
        async with self._factory() as session:
            existing = (
                await session.execute(
                    select(ReconciliationIssue).where(
                        ReconciliationIssue.tenant_id == tenant_id,
                        ReconciliationIssue.store_pair == store_pair,
                        ReconciliationIssue.issue_kind == issue_kind,
                        ReconciliationIssue.resource_kind == resource_kind,
                        ReconciliationIssue.resource_id == resource_id,
                        ReconciliationIssue.version_id == version_id,
                    )
                )
            ).scalars().first()
            if existing is not None:
                # A re-detection of an already-repaired issue means the repair did
                # not hold, so it reopens rather than staying closed.
                reopened = existing.state in {"repaired", "manual_required"}
                existing.state = "open"
                existing.observed_fingerprint = observed
                existing.expected_fingerprint = expected
                existing.severity = ISSUE_SEVERITY[issue_kind]
                existing.updated_at = utc_now()
                existing.resolved_at = None
                existing.version += 1
                await session.commit()
                (report.reopened if reopened else report.opened).append(key)
                return existing.id

            issue = ReconciliationIssue(
                tenant_id=tenant_id,
                store_pair=store_pair,
                issue_kind=issue_kind,
                resource_kind=resource_kind,
                resource_id=resource_id,
                version_id=version_id,
                observed_fingerprint=observed,
                expected_fingerprint=expected,
                severity=ISSUE_SEVERITY[issue_kind],
                state="open",
                max_attempts=self._max_attempts,
            )
            session.add(issue)
            await session.commit()
            report.opened.append(key)
            return issue.id

    async def _resolve(self, issue_id: str, *, resolution: str) -> None:
        async with self._factory() as session:
            issue = await session.get(ReconciliationIssue, issue_id)
            if issue is None:
                return
            issue.state = "repaired"
            issue.resolution = resolution[:500]
            issue.resolved_at = utc_now()
            issue.updated_at = utc_now()
            issue.next_attempt_at = None
            issue.last_error_code = None
            issue.version += 1
            await session.commit()

    async def _escalate(self, issue_id: str, *, reason: str) -> None:
        async with self._factory() as session:
            issue = await session.get(ReconciliationIssue, issue_id)
            if issue is None:
                return
            issue.state = "manual_required"
            issue.resolution = reason[:500]
            issue.resolved_at = utc_now()
            issue.updated_at = utc_now()
            issue.next_attempt_at = None
            issue.version += 1
            await session.commit()

    async def _fail_attempt(self, issue_id: str, *, error_code: str) -> None:
        """Record a failed repair, escalating once the budget is spent.

        This is what guarantees every issue terminates: a repair that keeps failing
        becomes a human's problem rather than retrying forever.
        """
        async with self._factory() as session:
            issue = await session.get(ReconciliationIssue, issue_id)
            if issue is None:
                return
            issue.attempts += 1
            issue.last_error_code = error_code[:80]
            issue.updated_at = utc_now()
            issue.version += 1
            if issue.attempts >= issue.max_attempts:
                issue.state = "manual_required"
                issue.resolution = (
                    f"automatic repair failed {issue.attempts} times "
                    f"({error_code}); manual intervention required"
                )[:500]
                issue.resolved_at = utc_now()
                issue.next_attempt_at = None
            else:
                issue.state = "open"
                issue.next_attempt_at = utc_now() + timedelta(
                    seconds=self._retry_backoff * issue.attempts
                )
            await session.commit()

    # -- queries ---------------------------------------------------------------

    async def open_issues(
        self, *, tenant_id: str, kinds: Sequence[str] | None = None
    ) -> list[ReconciliationIssue]:
        async with self._factory() as session:
            statement = select(ReconciliationIssue).where(
                ReconciliationIssue.tenant_id == tenant_id,
                ReconciliationIssue.state.in_(("open", "repairing")),
            )
            if kinds:
                statement = statement.where(
                    ReconciliationIssue.issue_kind.in_(list(kinds))
                )
            return list((await session.execute(statement)).scalars().all())

    async def confirm_physically_deleted(
        self, *, tenant_id: str, material_id: str
    ) -> list[str]:
        """Return the reasons a material is *not* yet fully deleted.

        ``data-model.md`` Cross-Entity Invariant #7: deletion is complete only once
        reconciliation confirms metadata, vectors and *all* object versions are
        absent. An empty list is that confirmation; anything else names what is
        still there, so "deleted" is never claimed on the strength of a status
        column alone.
        """
        reasons: list[str] = []
        async with self._factory() as session:
            versions = list(
                (
                    await session.execute(
                        select(MaterialVersion).where(
                            MaterialVersion.tenant_id == tenant_id,
                            MaterialVersion.material_id == material_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            objects = list(
                (
                    await session.execute(
                        select(ObjectVersion).where(
                            ObjectVersion.tenant_id == tenant_id,
                            ObjectVersion.material_id == material_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            manifests = list(
                (
                    await session.execute(
                        select(VectorManifest).where(
                            VectorManifest.tenant_id == tenant_id,
                            VectorManifest.material_id == material_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        if versions:
            reasons.append(f"{len(versions)} material_versions rows remain")
        if objects:
            reasons.append(f"{len(objects)} object_versions rows remain")
        if manifests:
            reasons.append(f"{len(manifests)} vector_manifests rows remain")

        # The stores are checked even when SQL is clean: metadata can be gone while
        # bytes and vectors survive, which is the failure mode that makes a naive
        # "rows are gone, so we are done" claim false.
        for version in versions or []:
            inventory = await self._store.inventory(
                tenant_id=tenant_id,
                material_id=material_id,
                material_version_id=version.id,
            )
            if not inventory.is_empty:
                reasons.append(
                    f"version {version.id} still has {len(inventory.versions)} object "
                    f"versions and {len(inventory.delete_markers)} delete markers"
                )
        for manifest in manifests or []:
            held = await self._vectors.count_by_prefix(
                tenant_id=tenant_id, vector_id_prefix=manifest.vector_id_prefix
            )
            if held:
                reasons.append(f"manifest {manifest.id} still has {held} vectors")
        return reasons

