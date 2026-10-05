"""T086 [US1] the storage authority switch and its Stage 9 removal gate.

Stage 5 moves the production authority for document bytes to versioned object
storage and for vectors to Milvus. The host-local ``UPLOAD_DIR`` writer and the
LightRAG workspace stay only as migration adapters, and the thing that makes that
claim honest rather than aspirational is the counter: they may be deleted in Stage
9 only once zero use has held across a release window.

So these tests pin the two halves of the switch:

* **which authority is in force**, including that production cannot silently fall
  back to a host-local one -- a per-host file means two API instances answer from
  different copies of the truth, which is worse than failing;
* **that every legacy use is counted**, that an unknown adapter name cannot be
  counted at all (it would make "zero use" a false clearance to delete live code),
  and that the counter records no document content.

The versioned path itself is proven against live Milvus and MinIO in T076; here it
is the *routing decision* that is under test, which needs no infrastructure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core.config import Settings
from backend.app.services.document_service import (
    MATERIAL_REFERENCE_PREFIX,
    _material_reference,
)
from backend.app.storage.authority import (
    LEGACY_ADAPTERS,
    StorageAuthority,
    StorageAuthorityTelemetry,
    StorageAuthorityUnavailable,
    legacy_usage_event,
    resolve_storage_authority,
)


def _production_like(**overrides):
    """A minimal stand-in for production settings.

    A real ``Settings(ENVIRONMENT="production", ...)`` cannot be constructed here:
    it validates the whole production contract at init and rejects SQLite, a
    missing broker and an enabled UPLOAD_DIR -- which is correct, and is itself
    covered by the config suite. ``resolve_storage_authority`` reads exactly three
    attributes, so a namespace carrying those three exercises the real function
    without having to stand up a production-shaped configuration.
    """
    values = {
        "ENVIRONMENT": "production",
        "OBJECT_STORE_ENDPOINT_URL": "https://minio.internal:9000",
        "MILVUS_URI": "https://milvus.internal:19530",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": "sqlite://",
        "LOG_DIR": Path("logs"),
        "SECRET_KEY": "x" * 32,
        "BOOTSTRAP_ADMIN_PASSWORD": "pw",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


# -- which authority is in force ---------------------------------------------


def test_versioned_authority_requires_configuration_and_wiring() -> None:
    """Both stores configured *and* the pipeline wired; configuration alone is not enough.

    ``MILVUS_URI`` and ``OBJECT_STORE_ENDPOINT_URL`` ship with non-empty defaults,
    so treating configuration as proof would route a host with no reachable stores
    into the versioned path and fail every upload.
    """
    settings = _settings()
    assert (
        resolve_storage_authority(settings, pipeline_available=True)
        is StorageAuthority.MATERIAL_VERSION
    )
    assert (
        resolve_storage_authority(settings, pipeline_available=False)
        is StorageAuthority.LEGACY_LOCAL_FILE
    )


def test_missing_stores_fall_back_to_the_legacy_adapter_in_development() -> None:
    """A dev host with no object store or Milvus keeps working, on the adapter."""
    for overrides in (
        {"OBJECT_STORE_ENDPOINT_URL": ""},
        {"MILVUS_URI": ""},
    ):
        settings = _settings(**overrides)
        assert (
            resolve_storage_authority(settings, pipeline_available=True)
            is StorageAuthority.LEGACY_LOCAL_FILE
        )


@pytest.mark.parametrize(
    "overrides,pipeline",
    [
        ({"OBJECT_STORE_ENDPOINT_URL": ""}, True),
        ({"MILVUS_URI": ""}, True),
        ({}, False),
    ],
)
def test_production_refuses_to_fall_back(overrides: dict, pipeline: bool) -> None:
    """Production never reports the legacy authority; it raises instead.

    Degrading there would give each API instance its own private copy of the
    truth, so an outright refusal is the safer failure.
    """
    settings = _production_like(**overrides)
    with pytest.raises(StorageAuthorityUnavailable) as caught:
        resolve_storage_authority(settings, pipeline_available=pipeline)
    message = str(caught.value)
    assert "production requires the versioned storage authority" in message
    # The message names what is missing, so an operator can act on it.
    assert any(
        token in message
        for token in ("OBJECT_STORE_ENDPOINT_URL", "MILVUS_URI", "material pipeline")
    )


def test_production_accepts_the_versioned_authority() -> None:
    settings = _production_like()
    assert (
        resolve_storage_authority(settings, pipeline_available=True)
        is StorageAuthority.MATERIAL_VERSION
    )


# -- the Stage 9 removal gate ------------------------------------------------


def test_both_legacy_adapters_are_registered() -> None:
    """The two host-local paths Stage 9 must be able to delete."""
    assert set(LEGACY_ADAPTERS) == {"local_file_write", "lightrag_index"}


def test_telemetry_starts_clear_and_counts_each_use() -> None:
    telemetry = StorageAuthorityTelemetry()
    assert telemetry.zero_use_over_window() is True
    assert telemetry.snapshot() == {"local_file_write": 0, "lightrag_index": 0}

    telemetry.record(
        legacy_usage_event(
            adapter="local_file_write",
            tenant_id="tenant-1",
            resource_kind="knowledge_document",
            resource_id="doc-1",
            reason="no object store on this host",
        )
    )
    telemetry.record(
        legacy_usage_event(
            adapter="lightrag_index",
            tenant_id="tenant-1",
            resource_kind="knowledge_document",
            resource_id="doc-1",
            reason="host-local workspace index",
        )
    )
    assert telemetry.zero_use_over_window() is False, (
        "a used adapter must not read as removable"
    )
    assert telemetry.usage_count("local_file_write") == 1
    assert telemetry.usage_count("lightrag_index") == 1
    assert telemetry.total_usage() == 2
    assert telemetry.snapshot() == {"local_file_write": 1, "lightrag_index": 1}


def test_an_unregistered_adapter_cannot_be_counted() -> None:
    """A new legacy path must be registered, not silently counted or dropped.

    If it were accepted under a catch-all, the removal check would report zero use
    for code that is still live and clear it for deletion.
    """
    with pytest.raises(ValueError, match="LEGACY_ADAPTERS"):
        legacy_usage_event(
            adapter="some_new_host_local_thing",
            tenant_id="tenant-1",
            resource_kind="knowledge_document",
            resource_id="doc-1",
            reason="added later without registering",
        )


def test_telemetry_events_carry_no_document_content() -> None:
    """The ledger records metadata only -- never a body, title or host path."""
    telemetry = StorageAuthorityTelemetry()
    telemetry.record(
        legacy_usage_event(
            adapter="local_file_write",
            tenant_id="tenant-1",
            resource_kind="knowledge_document",
            resource_id="doc-1",
            reason="no object store on this host",
        )
    )
    event = telemetry.events[0]
    assert set(vars(event)) == {
        "adapter",
        "authority",
        "tenant_id",
        "resource_kind",
        "resource_id",
        "reason",
    }, "a new field on the usage event risks carrying content into the ledger"
    assert event.authority is StorageAuthority.LEGACY_LOCAL_FILE


# -- the projection reference -------------------------------------------------


def test_projected_reference_is_opaque_but_keeps_a_display_name() -> None:
    """A projected document row points at a material version, not a host path.

    The LightRAG adapters take ``Path(...).name`` off ``file_path`` for display, so
    the reference has to keep a usable name while carrying no filesystem location.
    """
    reference = _material_reference("ver-123", "reimbursement-policy.pdf")
    assert reference.startswith(f"{MATERIAL_REFERENCE_PREFIX}/")
    assert "ver-123" in reference
    assert Path(reference).name == "reimbursement-policy.pdf"
    # Nothing here looks like a host location.
    for marker in (":", "\\", "..", "uploads"):
        assert marker not in reference


def test_the_upload_service_exposes_the_authority_seam() -> None:
    """``upload_document`` must accept the pipeline and the telemetry ledger.

    Asserted on the signature because this is the seam the route uses: if it were
    dropped, uploads would silently return to writing host-local files with no
    counter and nothing would fail.
    """
    import inspect

    from backend.app.services.document_service import upload_document
    from backend.app.services.indexing_service import process_document_index

    upload_parameters = inspect.signature(upload_document).parameters
    assert {"pipeline", "telemetry"} <= set(upload_parameters)
    assert upload_parameters["pipeline"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "telemetry" in inspect.signature(process_document_index).parameters
