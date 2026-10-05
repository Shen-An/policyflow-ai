"""T086 [US1] which store is authoritative for document bytes and vectors.

``data-model.md`` Cross-Entity Invariant #5: exactly one authority exists for each
concern -- PostgreSQL for business state, the object store for bytes, Milvus for
vectors -- and *adapters do not become a second authority*. Before Stage 5 the
authority for both bytes and vectors was host-local: files under ``UPLOAD_DIR``
and a LightRAG workspace under ``RAG_WORKSPACE_DIR``. Those are per-host, so two
API instances disagree, and nothing about them is versioned or reconcilable.

This module is the switch. It resolves which authority is in force, and it counts
every use of the legacy one.

The counter is not decoration: it is the Stage 9 *deletion condition*, exactly as
``graph/compat.py`` does for the legacy graph adapter. The local-file writer and
the LightRAG indexer may only be removed once
:meth:`StorageAuthorityTelemetry.zero_use_over_window` reports zero legacy use
across a release window. Until then the legacy path coexists with -- never
replaces -- the versioned one.

Two deliberate asymmetries:

* **Production cannot fall back.** ``Settings.require_production_ready`` already
  refuses a production deployment whose ``UPLOAD_DIR`` or LightRAG workspace is
  enabled. :func:`resolve_storage_authority` therefore refuses to *report* the
  legacy authority in production rather than silently writing host-local files
  that only one instance can read.
* **Telemetry records no content.** Only the adapter name, the authority, the
  tenant and the resource id -- never a document body, a title or a host path.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "LEGACY_ADAPTERS",
    "StorageAuthority",
    "StorageAuthorityTelemetry",
    "StorageAuthorityUnavailable",
    "legacy_usage_event",
    "resolve_storage_authority",
]


class StorageAuthority(StrEnum):
    """Who owns document bytes and their vectors."""

    #: Stage 5: versioned object storage for bytes, Milvus for vectors,
    #: PostgreSQL ``MaterialVersion`` for the metadata that ties them together.
    MATERIAL_VERSION = "material_version"
    #: Pre-Stage-5: host-local files plus a host-local LightRAG workspace. A
    #: migration adapter with a Stage 9 removal condition, never a target state.
    LEGACY_LOCAL_FILE = "legacy_local_file"


#: Legacy adapter names the telemetry counts. Listed as data so the Stage 9
#: removal check cannot silently miss one that was added later.
LEGACY_ADAPTERS: tuple[str, ...] = ("local_file_write", "lightrag_index")


class StorageAuthorityUnavailable(RuntimeError):  # noqa: N818 - reads as a condition
    """The configured authority cannot serve this deployment.

    Raised instead of degrading to the legacy path: a silent fallback to
    host-local files would give each API instance its own private copy of the
    truth, which is the exact failure Stage 5 exists to remove.
    """


@dataclass(frozen=True)
class LegacyUsageEvent:
    """One use of a legacy storage adapter. Carries no document content."""

    adapter: str
    authority: StorageAuthority
    tenant_id: str | None
    resource_kind: str
    resource_id: str | None
    reason: str


def legacy_usage_event(
    *,
    adapter: str,
    tenant_id: str | None,
    resource_kind: str,
    resource_id: str | None,
    reason: str,
) -> LegacyUsageEvent:
    """Build a legacy-usage event, rejecting an unknown adapter name.

    Unknown names are rejected rather than counted under a catch-all: an adapter
    the removal check does not know about would make "zero legacy use" a false
    clearance to delete code that is still live.
    """
    if adapter not in LEGACY_ADAPTERS:
        raise ValueError(
            f"unknown legacy storage adapter {adapter!r}; add it to LEGACY_ADAPTERS "
            "so the Stage 9 removal check counts it"
        )
    return LegacyUsageEvent(
        adapter=adapter,
        authority=StorageAuthority.LEGACY_LOCAL_FILE,
        tenant_id=tenant_id,
        resource_kind=resource_kind,
        resource_id=resource_id,
        reason=reason,
    )


@dataclass
class StorageAuthorityTelemetry:
    """Counts legacy storage-adapter usage; drives the Stage 9 removal decision."""

    events: list[LegacyUsageEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def record(self, event: LegacyUsageEvent) -> None:
        self._counts[event.adapter] += 1
        # The event holds metadata only; it is safe to retain in full.
        self.events.append(event)

    def usage_count(self, adapter: str) -> int:
        return self._counts[adapter]

    def total_usage(self) -> int:
        return sum(self._counts.values())

    def zero_use_over_window(self) -> bool:
        """True only when no legacy storage adapter has been used.

        The Stage 9 gate: ``UPLOAD_DIR`` writing and the LightRAG indexer may be
        deleted only when this has held across a release window.
        """
        return self.total_usage() == 0

    def snapshot(self) -> dict[str, int]:
        """Per-adapter counts, for the removal ledger and capacity evidence."""
        return {adapter: self._counts[adapter] for adapter in LEGACY_ADAPTERS}


def _is_configured(value: Any) -> bool:
    """Whether a settings value names a real endpoint rather than a placeholder."""
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"none", "disabled"}


def resolve_storage_authority(
    settings: Any, *, pipeline_available: bool
) -> StorageAuthority:
    """Return the authority in force for this request.

    Two conditions, both required: the Stage-5 stores must be *configured* (which
    the production guardrails already force) and the material pipeline must
    actually be *wired* on this process. Configuration alone is not enough --
    ``MILVUS_URI`` and ``OBJECT_STORE_ENDPOINT_URL`` carry non-empty defaults, so
    treating them as proof would route a host with no reachable stores into the
    versioned path and fail every upload.

    In production both conditions must hold: a host-local authority there would
    mean each API instance answers from its own private copy of the truth, so the
    absence is raised rather than degraded. In development the legacy authority is
    returned and every use of it is counted against the Stage 9 removal gate.
    """
    environment = str(getattr(settings, "ENVIRONMENT", "") or "").strip().lower()
    has_object_store = _is_configured(getattr(settings, "OBJECT_STORE_ENDPOINT_URL", None))
    has_vectors = _is_configured(getattr(settings, "MILVUS_URI", None))

    if has_object_store and has_vectors and pipeline_available:
        return StorageAuthority.MATERIAL_VERSION
    if environment == "production":
        missing = [
            name
            for name, present in (
                ("OBJECT_STORE_ENDPOINT_URL", has_object_store),
                ("MILVUS_URI", has_vectors),
                ("material pipeline wiring", pipeline_available),
            )
            if not present
        ]
        raise StorageAuthorityUnavailable(
            "production requires the versioned storage authority; not available: "
            + ", ".join(missing)
        )
    return StorageAuthority.LEGACY_LOCAL_FILE
