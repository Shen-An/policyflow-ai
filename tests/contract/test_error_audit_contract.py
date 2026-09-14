"""Contract tests for the stable error vocabulary, the audit sink and telemetry.

Covers task T021: stable error ``code``/``retryable``/server-controlled delay,
404 non-enumeration, field redaction, append-only audit with ``event_id``
idempotency, ``run_id`` propagation and the metric-label policy.

The suites are pure unit tests over the in-memory audit store plus one
SQLite-backed check of the ORM persistence path; no PostgreSQL, no collector and
no network access are required.
"""

from __future__ import annotations

import base64
import inspect
import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel

from backend.app.core.exceptions import ApplicationError
from backend.app.observability import audit as audit_module
from backend.app.observability.audit import (
    AuditContentRejectedError,
    AuditFailureReason,
    AuditOutcome,
    AuditPersistence,
    AuditRecord,
    AuditSink,
    AuditSinkUnavailableError,
    GuardedAuditSink,
    InMemoryAuditPersistence,
    SqlAlchemyAuditPersistence,
    build_audit_record,
    is_critical_action,
    new_event_id,
    sanitize_audit_metadata,
)
from backend.app.observability.errors import (
    ERROR_SEMANTICS,
    MAX_RETRY_AFTER_SECONDS,
    ContractError,
    ErrorCode,
    ResourceVisibility,
    resolve_resource_visibility,
    resource_not_found,
    semantics_for,
)
from backend.app.observability.guards import (
    PATH_MARKER,
    PROVIDER_PAYLOAD_MARKER,
    is_uuid_like,
)
from backend.app.observability.telemetry import (
    ATTR_RUN_ID,
    ATTR_TENANT_ID,
    OTLP_ENDPOINT_ENV,
    TELEMETRY_ENABLED_ENV,
    TelemetryPolicyError,
    bind_correlation,
    configure_telemetry,
    correlation_attributes,
    correlation_context,
    current_run_id,
    otel_available,
    record_api_request,
    record_authorization_decision,
    record_db_pool_usage,
    record_error,
    reset_telemetry,
    span,
    telemetry_configuration,
    validate_metric_labels,
    validate_span_attributes,
)

CONTRACT_CODES = {
    "AUTH_FORBIDDEN",
    "TENANT_NOT_FOUND",
    "RESOURCE_NOT_FOUND",
    "VERSION_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "APPROVAL_REQUIRED",
    "APPROVAL_STALE",
    "QUOTA_EXCEEDED",
    "CAPACITY_SATURATED",
    "RETRIEVAL_UNAVAILABLE",
    "INSUFFICIENT_EVIDENCE",
    "SANDBOX_POLICY_DENIED",
    "SANDBOX_LIMIT_EXCEEDED",
    "CONNECTOR_UNKNOWN_OUTCOME",
    "RECOVERABLE_FAILURE",
    "TERMINAL_FAILURE",
}


# --- test doubles ----------------------------------------------------------


class _RecordingInstrument:
    """Collect measurements and their labels instead of exporting them."""

    def __init__(self, name: str, measurements: list[dict[str, Any]]) -> None:
        self._name = name
        self._measurements = measurements

    def add(self, amount: Any, attributes: Mapping[str, Any] | None = None) -> None:
        self._measurements.append(
            {"name": self._name, "amount": amount, "attributes": dict(attributes or {})}
        )

    def record(self, amount: Any, attributes: Mapping[str, Any] | None = None) -> None:
        self.add(amount, attributes)


class _RecordingMeter:
    """Minimal meter that records every instrument creation and measurement."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.measurements: list[dict[str, Any]] = []

    def _instrument(self, name: str) -> _RecordingInstrument:
        self.created.append(name)
        return _RecordingInstrument(name, self.measurements)

    def create_counter(self, name: str, **kwargs: Any) -> _RecordingInstrument:
        return self._instrument(name)

    def create_histogram(self, name: str, **kwargs: Any) -> _RecordingInstrument:
        return self._instrument(name)

    def create_up_down_counter(self, name: str, **kwargs: Any) -> _RecordingInstrument:
        return self._instrument(name)


class _SpanRecorder:
    """In-memory SDK tracer so span attributes can be asserted without a collector."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer("policyflow-tests")

    def finished(self) -> Sequence[Any]:
        return list(self.exporter.get_finished_spans())


class _TelemetryRecorder:
    """Injected meter and SDK tracer used to observe the recording helpers."""

    def __init__(self) -> None:
        self.meter = _RecordingMeter()
        self.spans = _SpanRecorder()

    @property
    def measurements(self) -> list[dict[str, Any]]:
        return self.meter.measurements


class _FailingStore:
    """Audit store whose writes always fail."""

    def __init__(self) -> None:
        self.attempts = 0

    async def insert(self, record: AuditRecord) -> bool:
        self.attempts += 1
        raise RuntimeError("audit store unreachable")

    async def get(self, event_id: str) -> AuditRecord | None:
        raise RuntimeError("audit store unreachable")

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditRecord]:
        raise RuntimeError("audit store unreachable")


class _DuplicateWithoutRowStore:
    """Store that reports a duplicate yet cannot return the stored row."""

    async def insert(self, record: AuditRecord) -> bool:
        return False

    async def get(self, event_id: str) -> AuditRecord | None:
        return None

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditRecord]:
        return ()


class _StandInAuditEvent(SQLModel, table=True):
    """Stand-in for the ``AuditEvent`` model, mirroring its storage shape.

    Only the shape matters here: the ORM persistence path is exercised against a
    real table with the ``event_id`` unique constraint it relies on, using the
    split resource reference and ``occurred_at`` timestamp of the landed model.
    """

    __tablename__ = "stand_in_audit_event"

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(index=True, unique=True, max_length=36)
    tenant_id: str = Field(max_length=36)
    run_id: str | None = Field(default=None, max_length=36)
    actor_ref: str = Field(max_length=255)
    action: str = Field(max_length=128)
    resource_kind: str = Field(default="", max_length=60)
    resource_id: str | None = Field(default=None, max_length=64)
    authorization_version: int = Field(default=0)
    outcome: str = Field(max_length=32)
    reason_code: str | None = Field(default=None, max_length=64)
    redacted_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    request_id: str | None = Field(default=None, max_length=128)
    trace_id: str | None = Field(default=None, max_length=32)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- fixtures and helpers --------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_telemetry() -> Iterator[None]:
    """Isolate telemetry state, correlation bindings and pool snapshots per test."""
    reset_telemetry()
    yield
    reset_telemetry()


@pytest.fixture
def store() -> InMemoryAuditPersistence:
    return InMemoryAuditPersistence()


@pytest.fixture
def sink(store: InMemoryAuditPersistence) -> GuardedAuditSink:
    return GuardedAuditSink(store)


@pytest.fixture
def recorder() -> _TelemetryRecorder:
    """Telemetry configured with a recording meter and an in-memory SDK tracer."""
    injected = _TelemetryRecorder()
    configure_telemetry(meter=injected.meter, tracer=injected.spans.tracer)
    return injected


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": new_event_id(),
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "actor_ref": "user:8f14e45f-ea0a-4b3b-9a1f-2f5c1b6b1a11",
        "action": "read.policy",
        "resource_ref": "material_version:42",
        "authorization_version": 7,
        "outcome": AuditOutcome.ALLOWED.value,
        "reason_code": None,
        "redacted_metadata": {"route": "/api/v1/kb/{kb_id}"},
    }
    event.update(overrides)
    return event


async def _append(sink: AuditSink, **overrides: Any) -> AuditRecord:
    event = _event(**overrides)
    return await sink.append(**event)


def _serialized(record: AuditRecord) -> str:
    return json.dumps(record.redacted_metadata, ensure_ascii=False, default=str)


def _violations(exc: BaseException) -> tuple[str, ...]:
    assert isinstance(exc, AuditContentRejectedError)
    return exc.violations


class _FakeTableModel:
    """Minimal stand-in exposing the ``__table__`` shape ``_column_values`` reads."""

    def __init__(self, table: Any) -> None:
        self.__table__ = table


def _fake_model(*columns: Any) -> _FakeTableModel:
    return _FakeTableModel(Table("audit_mapping_probe", MetaData(), *columns))


def _durable_record() -> AuditRecord:
    return build_audit_record(**_event())


# --- stable error vocabulary ----------------------------------------------


def test_error_vocabulary_covers_the_contract_codes() -> None:
    declared = {code.value for code in ErrorCode}
    assert CONTRACT_CODES <= declared
    assert all(code.value == code.name for code in ErrorCode)
    assert set(ERROR_SEMANTICS) == set(ErrorCode)


def test_undeclared_codes_are_rejected() -> None:
    with pytest.raises(ValueError):
        ContractError("NOT_A_CONTRACT_CODE")
    assert semantics_for("NOT_A_CONTRACT_CODE") is None


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("RESOURCE_NOT_FOUND", 404),
        ("TENANT_NOT_FOUND", 404),
        ("AUTH_FORBIDDEN", 403),
        ("VERSION_CONFLICT", 409),
        ("APPROVAL_REQUIRED", 409),
        ("QUOTA_EXCEEDED", 429),
        ("CAPACITY_SATURATED", 503),
        ("RETRIEVAL_UNAVAILABLE", 503),
        ("CONNECTOR_UNKNOWN_OUTCOME", 502),
        ("TERMINAL_FAILURE", 500),
    ],
)
def test_error_codes_declare_stable_status(code: str, status_code: int) -> None:
    error = ContractError(code)
    assert error.code == code
    assert isinstance(error.code, str)
    assert error.status_code == status_code
    assert error.message
    assert error.details == {}


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("AUTH_FORBIDDEN", False),
        ("RESOURCE_NOT_FOUND", False),
        ("VERSION_CONFLICT", False),
        ("IDEMPOTENCY_CONFLICT", False),
        ("APPROVAL_STALE", False),
        ("INSUFFICIENT_EVIDENCE", False),
        ("SANDBOX_POLICY_DENIED", False),
        ("CONNECTOR_UNKNOWN_OUTCOME", False),
        ("TERMINAL_FAILURE", False),
        ("QUOTA_EXCEEDED", True),
        ("CAPACITY_SATURATED", True),
        ("RETRIEVAL_UNAVAILABLE", True),
        ("SANDBOX_LIMIT_EXCEEDED", True),
        ("RECOVERABLE_FAILURE", True),
    ],
)
def test_retry_semantics_are_declared_per_code(code: str, retryable: bool) -> None:
    error = ContractError(code)
    assert error.retryable is retryable
    if retryable:
        assert error.retry_after_seconds is not None
        assert error.retry_after_header() == {"Retry-After": str(int(error.retry_after_seconds))}
    else:
        assert error.retry_after_seconds is None
        assert error.retry_after_header() == {}


def test_server_controlled_delay_is_optional_and_never_client_supplied() -> None:
    default = ContractError("RECOVERABLE_FAILURE")
    assert default.retry_after_seconds == pytest.approx(5.0)

    negotiated = ContractError("RECOVERABLE_FAILURE", retry_after_seconds=12.5)
    assert negotiated.retry_after_header() == {"Retry-After": "13"}
    assert "retry_after_seconds" in negotiated.public_payload()

    with pytest.raises(ValueError):
        ContractError("RESOURCE_NOT_FOUND", retry_after_seconds=5.0)


@pytest.mark.parametrize("delay", [0.0, -1.0, MAX_RETRY_AFTER_SECONDS + 1])
def test_retry_delay_must_be_positive_and_capped(delay: float) -> None:
    with pytest.raises(ValueError):
        ContractError("QUOTA_EXCEEDED", retry_after_seconds=delay)


def test_error_payload_exposes_only_stable_fields() -> None:
    payload = ContractError("QUOTA_EXCEEDED").public_payload()
    assert set(payload) == {
        "code",
        "message",
        "details",
        "retryable",
        "retry_after_seconds",
    }
    assert payload["code"] == "QUOTA_EXCEEDED"


@pytest.mark.parametrize(
    "message",
    [
        r"failed to read C:\Users\alice\policy.docx",
        "cannot open /etc/passwd",
        "provider said Bearer abcdefgh12345678",
        "api_key=sk-live-abcdefghijklmnop",
    ],
)
def test_error_content_cannot_leak_paths_or_credentials(message: str) -> None:
    with pytest.raises(ValueError):
        ContractError("RECOVERABLE_FAILURE", message=message)


def test_error_details_cannot_leak_paths_or_credentials() -> None:
    with pytest.raises(ValueError):
        ContractError("RECOVERABLE_FAILURE", details={"storage_key": "/var/lib/policyflow/raw.bin"})
    with pytest.raises(ValueError):
        ContractError("RECOVERABLE_FAILURE", details={"credential": "password=hunter2secret"})


# --- 404 non-enumeration ---------------------------------------------------


def test_missing_and_foreign_tenant_resources_are_indistinguishable() -> None:
    absent = resolve_resource_visibility(ResourceVisibility.ABSENT)
    foreign = resolve_resource_visibility(ResourceVisibility.FOREIGN_TENANT)
    assert absent is not None
    assert foreign is not None
    assert absent.public_payload() == foreign.public_payload()
    assert absent.code == foreign.code == "RESOURCE_NOT_FOUND"
    assert absent.status_code == foreign.status_code == 404
    assert absent.details == foreign.details == {}
    assert str(absent) == str(foreign) == "Resource not found"


def test_not_found_factory_cannot_be_told_why_a_resource_is_invisible() -> None:
    parameters = list(inspect.signature(resolve_resource_visibility).parameters)
    assert parameters == ["visibility"]
    assert not hasattr(resource_not_found(), "visibility")


def test_not_found_error_is_a_client_safe_application_error() -> None:
    error = resource_not_found()
    assert isinstance(error, ApplicationError)
    assert error.status_code == 404
    assert error.retryable is False
    assert error.public_payload()["details"] == {}
    serialized = json.dumps(error.public_payload(), default=str)
    assert "tenant" not in serialized
    assert "resource_ref" not in serialized
    assert ":\\" not in serialized
    assert "/etc" not in serialized


def test_visible_resource_does_not_raise_not_found() -> None:
    assert resolve_resource_visibility(ResourceVisibility.VISIBLE) is None
    assert resolve_resource_visibility("visible") is None
    with pytest.raises(ValueError):
        resolve_resource_visibility("unknown_visibility")


# --- audit sink: append-only and idempotent --------------------------------


def test_in_memory_store_satisfies_the_persistence_protocol() -> None:
    assert isinstance(InMemoryAuditPersistence(), AuditPersistence)
    assert isinstance(GuardedAuditSink(InMemoryAuditPersistence()), AuditSink)


async def test_append_persists_one_row_and_returns_it(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    event = _event()
    record = await sink.append(**event)
    assert record.event_id == event["event_id"]
    assert record.tenant_id == event["tenant_id"]
    assert record.run_id == event["run_id"]
    assert record.action == "read.policy"
    assert record.outcome == "allowed"
    assert record.authorization_version == 7
    assert len(store) == 1
    assert store.rows[0] == record


def test_generated_event_ids_are_uuid_strings() -> None:
    first, second = new_event_id(), new_event_id()
    assert first != second
    assert is_uuid_like(first)
    assert is_uuid_like(second)


async def test_duplicate_append_is_idempotent_and_append_only(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    event_id = new_event_id()
    first = await _append(sink, event_id=event_id, redacted_metadata={"note": "first write"})
    second = await _append(
        sink,
        event_id=event_id,
        outcome=AuditOutcome.DENIED.value,
        redacted_metadata={"note": "second write"},
    )
    assert len(store) == 1
    assert second == first
    assert store.rows[0].redacted_metadata == {"note": "first write"}
    assert store.rows[0].outcome == "allowed"


async def test_stored_rows_are_not_mutable_through_returned_records(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    record = await _append(sink, redacted_metadata={"note": "keep"})
    metadata = record.redacted_metadata
    assert isinstance(metadata, dict)
    metadata["injected"] = True
    assert store.rows[0].redacted_metadata == {"note": "keep"}


@pytest.mark.parametrize(
    ("overrides", "violation"),
    [
        ({"event_id": "not-a-uuid"}, "event_id_not_uuid"),
        ({"tenant_id": "acme"}, "tenant_id_not_uuid"),
        ({"run_id": "run-1"}, "run_id_not_uuid"),
        ({"outcome": "maybe"}, "outcome_unknown"),
        ({"authorization_version": -1}, "authorization_version_invalid"),
        ({"action": "Read Policy"}, "action_malformed"),
        ({"reason_code": "not-a-code"}, "reason_code_malformed"),
    ],
)
async def test_invalid_event_fields_are_rejected(
    sink: GuardedAuditSink,
    store: InMemoryAuditPersistence,
    overrides: dict[str, Any],
    violation: str,
) -> None:
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, **overrides)
    assert violation in _violations(excinfo.value)
    assert len(store) == 0


@pytest.mark.parametrize(
    "reference_field",
    ["actor_ref", "resource_ref"],
)
@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        r"C:\Users\alice\policy.docx",
        r"\\fs01\hr\policy.pdf",
        "Bearer abcdefgh12345678",
    ],
)
async def test_reference_fields_reject_host_paths_and_credentials(
    sink: GuardedAuditSink,
    store: InMemoryAuditPersistence,
    reference_field: str,
    value: str,
) -> None:
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, **{reference_field: value})
    assert any(item.startswith(reference_field) for item in _violations(excinfo.value))
    assert len(store) == 0


# --- audit sink: field redaction -------------------------------------------


async def test_credentials_are_redacted_before_persistence(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    record = await _append(
        sink,
        redacted_metadata={
            "password": "hunter2-secret",
            "api_key": "sk-live-abcdefghijklmnop",
            "note": "Authorization: Bearer abcdefgh12345678",
            "route": "/api/v1/kb/{kb_id}",
        },
    )
    serialized = _serialized(record)
    for secret in (
        "hunter2-secret",
        "sk-live-abcdefghijklmnop",
        "abcdefgh12345678",
    ):
        assert secret not in serialized
    assert serialized.count("[REDACTED]") >= 2
    assert "credential:key" in record.redactions
    assert any(item.startswith("credential:") for item in record.redactions)
    assert store.rows[0].redacted_metadata["_redaction"] == list(record.redactions)
    assert "password" in store.rows[0].redacted_metadata


@pytest.mark.parametrize(
    "host_path",
    [
        r"C:\Users\alice\policy.docx",
        r"\\fs01\hr\policy.pdf",
        "/etc/policyflow/secrets.env",
        "/home/alice/workspace/draft.docx",
    ],
)
async def test_host_paths_never_reach_the_persisted_row(
    sink: GuardedAuditSink, host_path: str
) -> None:
    record = await _append(sink, redacted_metadata={"note": f"opened {host_path} for review"})
    serialized = _serialized(record)
    assert PATH_MARKER in serialized
    for fragment in (host_path, "alice", "fs01", "secrets.env"):
        assert fragment not in serialized
    assert any(item.startswith("host_path:") for item in record.redactions)


async def test_raw_provider_payload_is_redacted(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    record = await _append(
        sink,
        redacted_metadata={
            "provider_response": {
                "model": "gpt-4o",
                "choices": [{"message": {"role": "assistant", "content": "raw answer"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
            "latency_ms": 42,
        },
    )
    serialized = _serialized(record)
    assert "choices" not in serialized
    assert "raw answer" not in serialized
    assert PROVIDER_PAYLOAD_MARKER in serialized
    assert "provider_payload" in record.redactions
    assert store.rows[0].redacted_metadata["latency_ms"] == 42


async def test_embedded_json_provider_payload_is_redacted(sink: GuardedAuditSink) -> None:
    payload = json.dumps({"choices": [{"finish_reason": "stop"}], "usage": {"total_tokens": 3}})
    record = await _append(sink, redacted_metadata={"provider_body": payload})
    serialized = _serialized(record)
    assert "choices" not in serialized
    assert "provider_payload" in record.redactions


async def test_unrestricted_file_bodies_are_rejected(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence, tmp_path: Path
) -> None:
    handle = (tmp_path / "policy.txt").open("w", encoding="utf-8")
    try:
        with pytest.raises(AuditContentRejectedError) as excinfo:
            await _append(sink, redacted_metadata={"body": handle})
        assert any(
            item.startswith("unsupported_value_type") for item in _violations(excinfo.value)
        )
    finally:
        handle.close()

    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, redacted_metadata={"body": b"binary policy body"})
    assert "file_body:binary_payload" in _violations(excinfo.value)
    assert len(store) == 0


@pytest.mark.parametrize(
    "oversized",
    ["x" * 5000, base64.b64encode(b"policy document body " * 60).decode()],
)
async def test_oversized_bodies_are_stripped_not_persisted(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence, oversized: str
) -> None:
    record = await _append(sink, redacted_metadata={"body": oversized})
    serialized = _serialized(record)
    assert "file_body" in record.redactions
    assert oversized[:64] not in serialized
    assert store.rows[0].redacted_metadata["body"].startswith("[REDACTED_BODY:")


async def test_oversized_metadata_document_is_rejected(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(
            sink,
            redacted_metadata={f"field_{index}": "y" * 1000 for index in range(40)},
        )
    assert "metadata_too_large" in _violations(excinfo.value)
    assert len(store) == 0


async def test_too_many_metadata_entries_are_rejected(sink: GuardedAuditSink) -> None:
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, redacted_metadata={f"field_{index}": index for index in range(400)})
    assert "metadata_too_many_entries" in _violations(excinfo.value)


async def test_deeply_nested_metadata_is_rejected(sink: GuardedAuditSink) -> None:
    nested: dict[str, Any] = {"level": 0}
    for level in range(1, 10):
        nested = {f"level_{level}": nested}
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, redacted_metadata=nested)
    assert "metadata_too_deep" in _violations(excinfo.value)


@pytest.mark.parametrize(
    ("metadata", "violation"),
    [
        ({"_redaction": ["credential"]}, "metadata_key_reserved"),
        ({"Bad-Key": "value"}, "metadata_key_malformed"),
        ({7: "value"}, "metadata_key_not_string"),
    ],
)
async def test_metadata_keys_are_validated(
    sink: GuardedAuditSink, metadata: dict[Any, Any], violation: str
) -> None:
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, redacted_metadata=metadata)
    assert violation in _violations(excinfo.value)


async def test_metadata_must_be_a_mapping(sink: GuardedAuditSink) -> None:
    with pytest.raises(AuditContentRejectedError) as excinfo:
        await _append(sink, redacted_metadata=["not", "a", "mapping"])
    assert "metadata_not_mapping" in _violations(excinfo.value)


def test_sanitizer_reports_what_it_stripped() -> None:
    cleaned, redactions = sanitize_audit_metadata({"note": "Bearer abcdefgh12345678"})
    assert redactions == ("credential:bearer_token",)
    assert cleaned["note"] == "[REDACTED]"
    assert cleaned["_redaction"] == ["credential:bearer_token"]

    empty, no_redactions = sanitize_audit_metadata(None)
    assert empty == {}
    assert no_redactions == ()


# --- audit sink: failure semantics ----------------------------------------


@pytest.mark.parametrize(
    ("action", "blocks"),
    [
        ("approval.execute", True),
        ("connector.submit", True),
        ("external.submit", True),
        ("read.policy", False),
        ("authorization.decision", False),
    ],
)
def test_critical_actions_are_classified(action: str, blocks: bool) -> None:
    assert is_critical_action(action) is blocks


@pytest.mark.parametrize("action", ["approval.execute", "connector.submit"])
async def test_audit_failure_blocks_completion_and_is_never_swallowed(action: str) -> None:
    failing = _FailingStore()
    guarded = GuardedAuditSink(failing)
    with pytest.raises(AuditSinkUnavailableError) as excinfo:
        await _append(guarded, action=action)
    error = excinfo.value
    assert error.blocks_completion is True
    assert error.recoverable is True
    assert error.retryable is True
    assert error.retry_after_seconds is not None
    assert error.retry_after_header()
    assert error.code == "AUDIT_UNAVAILABLE"
    assert isinstance(error, ContractError)
    assert isinstance(error, ApplicationError)
    assert error.reason == AuditFailureReason.STORE_ERROR
    assert error.status_code == 503
    assert "RuntimeError" in error.message
    assert failing.attempts == 1


async def test_non_blocking_audit_failure_still_raises() -> None:
    guarded = GuardedAuditSink(_FailingStore())
    with pytest.raises(AuditSinkUnavailableError) as excinfo:
        await _append(guarded, action="read.policy")
    assert excinfo.value.blocks_completion is False
    assert excinfo.value.recoverable is True


async def test_critical_flag_can_be_set_explicitly() -> None:
    guarded = GuardedAuditSink(_FailingStore())
    with pytest.raises(AuditSinkUnavailableError) as excinfo:
        await _append(guarded, action="read.policy", critical=True)
    assert excinfo.value.blocks_completion is True


async def test_duplicate_without_a_readable_row_is_unavailable() -> None:
    guarded = GuardedAuditSink(_DuplicateWithoutRowStore())
    with pytest.raises(AuditSinkUnavailableError) as excinfo:
        await _append(guarded)
    assert excinfo.value.reason == AuditFailureReason.DUPLICATE_NOT_READABLE
    assert excinfo.value.recoverable is True


# --- audit sink: run_id propagation ---------------------------------------


async def test_run_id_falls_back_to_the_bound_telemetry_context(
    sink: GuardedAuditSink,
) -> None:
    run_id = str(uuid4())
    with correlation_context(run_id=run_id, request_id="req-4711"):
        record = await _append(sink, run_id=None)
    assert record.run_id == run_id
    assert record.request_id == "req-4711"


async def test_explicit_run_id_wins_over_the_bound_context(sink: GuardedAuditSink) -> None:
    bound = str(uuid4())
    explicit = str(uuid4())
    with correlation_context(run_id=bound):
        record = await _append(sink, run_id=explicit)
    assert record.run_id == explicit


async def test_run_id_correlates_audit_rows_and_telemetry_state(
    sink: GuardedAuditSink, store: InMemoryAuditPersistence
) -> None:
    run_id = str(uuid4())
    with correlation_context(run_id=run_id, request_id="req-99"):
        assert current_run_id() == run_id
        record_authorization_decision("approve", allowed=True)
        record_error("RECOVERABLE_FAILURE", boundary="worker")
        record = await _append(sink, run_id=None)
    assert current_run_id() is None
    rows = await store.list_for_tenant(str(record.tenant_id), run_id=run_id)
    assert [row.event_id for row in rows] == [record.event_id]


# --- ORM persistence ------------------------------------------------------


async def test_orm_persistence_is_idempotent_on_the_event_id_unique_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(audit_module, "_audit_event_model", lambda: _StandInAuditEvent)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: _StandInAuditEvent.metadata.create_all(sync_connection)
            )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            sink = GuardedAuditSink(SqlAlchemyAuditPersistence(session))
            event_id = new_event_id()
            first = await _append(sink, event_id=event_id, redacted_metadata={"note": "durable"})
            duplicate = await _append(
                sink,
                event_id=event_id,
                redacted_metadata={"note": "second durable write"},
            )
            await session.commit()
            rows = await SqlAlchemyAuditPersistence(session).list_for_tenant(
                str(first.tenant_id), run_id=str(first.run_id)
            )
        assert duplicate.event_id == first.event_id
        assert duplicate.outcome == first.outcome
        assert duplicate.redacted_metadata == {"note": "durable"}
        assert [row.event_id for row in rows] == [event_id]
        assert rows[0].redacted_metadata == {"note": "durable"}
        assert rows[0].resource_ref == "material_version:42"
        assert isinstance(rows[0].recorded_at, datetime)
        assert rows[0].request_id == first.request_id
    finally:
        await engine.dispose()


async def test_orm_persistence_keeps_one_row_per_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(audit_module, "_audit_event_model", lambda: _StandInAuditEvent)
    tenant_id = str(uuid4())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: _StandInAuditEvent.metadata.create_all(sync_connection)
            )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            sink = GuardedAuditSink(SqlAlchemyAuditPersistence(session))
            first = await _append(sink, tenant_id=tenant_id)
            second = await _append(sink, tenant_id=tenant_id)
            await session.commit()
            rows = await SqlAlchemyAuditPersistence(session).list_for_tenant(tenant_id, limit=10)
        assert first.event_id != second.event_id
        assert [row.event_id for row in rows] == [first.event_id, second.event_id]
    finally:
        await engine.dispose()


def test_orm_persistence_constructs_without_importing_the_models() -> None:
    before = set(sys.modules)
    SqlAlchemyAuditPersistence(object())
    newly_imported = set(sys.modules) - before
    assert "backend.app.db.models" not in newly_imported
    assert inspect.iscoroutinefunction(SqlAlchemyAuditPersistence.insert)
    assert inspect.iscoroutinefunction(SqlAlchemyAuditPersistence.get)
    assert inspect.iscoroutinefunction(SqlAlchemyAuditPersistence.list_for_tenant)


def test_column_mapping_uses_a_direct_resource_ref_column() -> None:
    model = _fake_model(
        Column("event_id", String(36)),
        Column("tenant_id", String(36)),
        Column("resource_ref", String(255)),
    )
    values = audit_module._column_values(model, _durable_record())
    assert values["resource_ref"] == "material_version:42"
    assert "resource_kind" not in values


def test_column_mapping_splits_the_reference_for_the_landed_model_shape() -> None:
    model = _fake_model(
        Column("event_id", String(36)),
        Column("tenant_id", String(36)),
        Column("resource_kind", String(60)),
        Column("resource_id", String(64)),
        Column("occurred_at", DateTime()),
    )
    record = _durable_record()
    values = audit_module._column_values(model, record)
    assert values["resource_kind"] == "material_version"
    assert values["resource_id"] == "42"
    assert values["occurred_at"] == record.recorded_at
    assert "resource_ref" not in values


def test_column_mapping_rejects_values_that_do_not_fit_their_column() -> None:
    model = _fake_model(
        Column("event_id", String(36)),
        Column("tenant_id", String(36)),
        Column("resource_ref", String(4)),
    )
    with pytest.raises(AuditContentRejectedError) as excinfo:
        audit_module._column_values(model, _durable_record())
    assert "resource_ref_exceeds_column_length" in _violations(excinfo.value)


# --- telemetry: metric label policy ---------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "tenant_id",
        "tenant",
        "tenant.id",
        "user_id",
        "user",
        "actor_ref",
        "actor_id",
        "email",
        "user_email",
        "membership_id",
        "session_id",
        "run_id",
        "request_id",
        "trace_id",
        "resource_ref",
        "event_id",
        "idempotency_key",
    ],
)
def test_metric_labels_reject_identity_keys(key: str) -> None:
    with pytest.raises(TelemetryPolicyError) as excinfo:
        validate_metric_labels({key: "value"})
    assert excinfo.value.scope == "metric_label_key"
    assert excinfo.value.violation == "label_key_names_an_identifier"


@pytest.mark.parametrize(
    "value",
    [
        str(uuid4()),
        "a" * 32,
        "alice@example.com",
        "Bearer abcdefgh12345678",
        r"C:\Users\alice\policy.docx",
        "v" * 200,
        "line\nbreak",
    ],
)
def test_metric_label_values_reject_identifiers_and_unbounded_text(value: str) -> None:
    with pytest.raises(TelemetryPolicyError) as excinfo:
        validate_metric_labels({"route": value})
    assert excinfo.value.scope == "metric_label_value"


def test_tenant_id_is_rejected_as_a_metric_label_value() -> None:
    tenant_id = str(uuid4())
    with pytest.raises(TelemetryPolicyError) as excinfo:
        validate_metric_labels({"route": tenant_id})
    assert excinfo.value.scope == "metric_label_value"
    assert tenant_id not in str(excinfo.value)


def test_bounded_labels_are_accepted() -> None:
    labels = validate_metric_labels(
        {
            "method": "GET",
            "route": "/api/v1/kb/{kb_id}",
            "status_class": "2xx",
            "pool": "default",
            "state": "checked_out",
            "code": "QUOTA_EXCEEDED",
            "retryable": True,
            "boundary": "api",
            "action": "read",
            "decision": "allow",
            "reason_code": "NO_SCOPE",
        }
    )
    assert labels["retryable"] == "true"
    assert labels["route"] == "/api/v1/kb/{kb_id}"


def test_non_scalar_and_malformed_label_inputs_are_rejected() -> None:
    with pytest.raises(TelemetryPolicyError):
        validate_metric_labels({"route": ["/a", "/b"]})
    with pytest.raises(TelemetryPolicyError):
        validate_metric_labels({"route!name": "x"})
    with pytest.raises(TelemetryPolicyError):
        validate_metric_labels(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_label_keys_are_normalized_to_snake_case() -> None:
    assert validate_metric_labels({"Status-Class": "2xx"}) == {"status_class": "2xx"}
    assert validate_metric_labels({"HTTP.Method": "GET"}) == {"http_method": "GET"}


@pytest.mark.parametrize(
    "attribute",
    [
        {"policyflow.bearer": "Bearer abcdefgh12345678"},
        {"policyflow.path": "/etc/policyflow/config.yaml"},
        {"policyflow.blob": "z" * 600},
        {"policyflow.bad": {"nested": "mapping"}},
        {"Bad Attribute": "x"},
    ],
)
def test_span_attributes_reject_forbidden_content(attribute: dict[str, Any]) -> None:
    with pytest.raises(TelemetryPolicyError) as excinfo:
        validate_span_attributes(attribute)
    assert excinfo.value.scope == "span_attribute"


def test_span_attributes_may_carry_tenant_and_run_identifiers() -> None:
    run_id = str(uuid4())
    tenant_id = str(uuid4())
    with correlation_context(run_id=run_id, request_id="req-1"):
        attributes = correlation_attributes(tenant_id=tenant_id, actor_ref="user:1")
    assert attributes[ATTR_RUN_ID] == run_id
    assert attributes[ATTR_TENANT_ID] == tenant_id
    with pytest.raises(TelemetryPolicyError):
        validate_metric_labels({"tenant_id": tenant_id})


# --- telemetry: recording helpers -----------------------------------------


def test_metric_helpers_only_send_bounded_labels(recorder: _TelemetryRecorder) -> None:
    run_id = str(uuid4())
    tenant_id = str(uuid4())
    with correlation_context(run_id=run_id, request_id="req-7"):
        record_api_request(
            method="GET",
            route="/api/v1/kb/{kb_id}",
            status_code=200,
            duration_seconds=0.012,
        )
        record_error("QUOTA_EXCEEDED", boundary="api")
        record_authorization_decision("read", allowed=False, reason_code="NO_SCOPE")
        record_db_pool_usage(size=5, checked_in=4, checked_out=1, overflow=0)

    assert recorder.meter.created == [
        "policyflow.api.requests",
        "policyflow.api.request.duration",
        "policyflow.db.pool.connections",
        "policyflow.db.pool.size",
        "policyflow.errors",
        "policyflow.authorization.decisions",
    ]
    forbidden_keys = {"tenant_id", "user_id", "run_id", "request_id", "trace_id", "actor_ref"}
    for measurement in recorder.measurements:
        attributes = measurement["attributes"]
        assert not forbidden_keys & set(attributes)
        assert run_id not in attributes.values()
        assert tenant_id not in attributes.values()
    measurement_names = {item["name"] for item in recorder.measurements}
    assert "policyflow.api.requests" in measurement_names
    assert "policyflow.authorization.decisions" in measurement_names


def test_correlation_reaches_spans_but_never_labels(recorder: _TelemetryRecorder) -> None:
    run_id = str(uuid4())
    tenant_id = str(uuid4())
    with correlation_context(run_id=run_id, request_id="req-5"):
        with span("http.request", tenant_id=tenant_id):
            record_api_request(
                method="POST",
                route="/api/v1/chat",
                status_code=202,
                duration_seconds=0.5,
            )
            record_error("RETRIEVAL_UNAVAILABLE")
            record_authorization_decision("read", allowed=True)
            record_db_pool_usage(size=5, checked_in=4, checked_out=1, overflow=0)
    finished = recorder.spans.finished()
    assert len(finished) == 1
    attributes = dict(finished[0].attributes)
    assert attributes[ATTR_RUN_ID] == run_id
    assert attributes[ATTR_TENANT_ID] == tenant_id
    assert attributes["http.request.method"] == "POST"
    assert attributes["http.response.status_code"] == 202
    assert attributes["policyflow.error.code"] == "RETRIEVAL_UNAVAILABLE"
    assert attributes["policyflow.authorization.decision"] == "allow"
    assert attributes["db.pool.name"] == "default"
    assert attributes["db.pool.checked_out"] == 1
    for measurement in recorder.measurements:
        serialized = json.dumps(measurement["attributes"])
        assert run_id not in serialized
        assert tenant_id not in serialized


def test_record_error_uses_declared_retryability(recorder: _TelemetryRecorder) -> None:
    record_error("QUOTA_EXCEEDED")
    record_error("RESOURCE_NOT_FOUND")
    record_error("RECOVERABLE_FAILURE", retryable=False)
    record_error(ErrorCode.QUOTA_EXCEEDED)
    retryable_labels = [
        item["attributes"]["retryable"]
        for item in recorder.measurements
        if item["name"] == "policyflow.errors"
    ]
    assert retryable_labels == ["true", "false", "false", "true"]


def test_record_error_rejects_unstable_codes(recorder: _TelemetryRecorder) -> None:
    with pytest.raises(TelemetryPolicyError):
        record_error("quota exceeded")
    assert recorder.measurements == []


def test_record_api_request_requires_a_templated_route(
    recorder: _TelemetryRecorder,
) -> None:
    with pytest.raises(TelemetryPolicyError):
        record_api_request(
            method="GET",
            route=f"/api/v1/kb/{uuid4()}",
            status_code=200,
            duration_seconds=0.1,
        )
    with pytest.raises(TelemetryPolicyError):
        record_api_request(
            method="FETCH", route="/api/v1/kb", status_code=200, duration_seconds=0.1
        )
    with pytest.raises(TelemetryPolicyError):
        record_api_request(
            method="GET", route="/api/v1/kb", status_code=999, duration_seconds=0.1
        )
    assert recorder.measurements == []


def test_record_authorization_decision_counts_allow_and_deny(
    recorder: _TelemetryRecorder,
) -> None:
    record_authorization_decision("approve", allowed=True)
    record_authorization_decision("approve", allowed=False, reason_code="NO_SCOPE")
    decisions = [
        item["attributes"]
        for item in recorder.measurements
        if item["name"] == "policyflow.authorization.decisions"
    ]
    assert [item["decision"] for item in decisions] == ["allow", "deny"]
    assert [item["action"] for item in decisions] == ["approve", "approve"]
    assert decisions[1]["reason_code"] == "NO_SCOPE"

    with pytest.raises(TelemetryPolicyError):
        record_authorization_decision("Approve Resource", allowed=True)
    with pytest.raises(TelemetryPolicyError):
        record_authorization_decision(str(uuid4()), allowed=True)


def test_db_pool_usage_reports_absolute_snapshot_then_deltas(
    recorder: _TelemetryRecorder,
) -> None:
    first = record_db_pool_usage(size=5, checked_in=4, checked_out=1, overflow=0)
    assert first == {"size": 5, "checked_in": 4, "checked_out": 1, "overflow": 0}
    second = record_db_pool_usage(size=5, checked_in=5, checked_out=0, overflow=0)
    assert second == {"size": 0, "checked_in": 1, "checked_out": -1, "overflow": 0}
    third = record_db_pool_usage(pool_name="eval", size=2, checked_in=2, checked_out=0, overflow=0)
    assert third == {"size": 2, "checked_in": 2, "checked_out": 0, "overflow": 0}
    connection_labels = [
        item["attributes"]
        for item in recorder.measurements
        if item["name"] == "policyflow.db.pool.connections"
    ]
    assert {"pool": "default", "state": "checked_out"} in connection_labels
    assert {"pool": "eval", "state": "checked_in"} in connection_labels


@pytest.mark.parametrize(
    ("kwargs", "extra"),
    [
        ({"size": -1, "checked_in": 0, "checked_out": 0, "overflow": 0}, {}),
        ({"size": 1, "checked_in": 0, "checked_out": 0, "overflow": 0}, {"pool_name": str(uuid4())}),
        ({"size": 1, "checked_in": 0, "checked_out": 0, "overflow": 0}, {"pool_name": "p" * 200}),
    ],
)
def test_db_pool_usage_rejects_invalid_input(
    recorder: _TelemetryRecorder, kwargs: dict[str, Any], extra: dict[str, Any]
) -> None:
    with pytest.raises(TelemetryPolicyError):
        record_db_pool_usage(**kwargs, **extra)


# --- telemetry: correlation -----------------------------------------------


def test_correlation_context_binds_and_restores_identifiers() -> None:
    run_id = str(uuid4())
    other_run = str(uuid4())
    trace_id = "0" * 31 + "1"
    with correlation_context(run_id=run_id, request_id="req-1", trace_id=trace_id):
        assert current_run_id() == run_id
        assert correlation_attributes()[ATTR_RUN_ID] == run_id
        with correlation_context(run_id=other_run):
            assert current_run_id() == other_run
            assert correlation_attributes()["policyflow.request_id"] == "req-1"
        assert current_run_id() == run_id
    assert current_run_id() is None


def test_correlation_context_replace_clears_inherited_identifiers() -> None:
    run_id = str(uuid4())
    with correlation_context(run_id=run_id):
        with correlation_context(replace=True):
            assert current_run_id() is None
        assert current_run_id() == run_id


@pytest.mark.parametrize(
    "bindings",
    [
        {"run_id": "not-a-uuid"},
        {"run_id": ""},
        {"request_id": "bad\nrequest"},
        {"trace_id": "zz"},
    ],
)
def test_correlation_rejects_malformed_identifiers(bindings: dict[str, str]) -> None:
    with pytest.raises(TelemetryPolicyError) as excinfo:
        bind_correlation(**bindings)
    assert excinfo.value.scope == "correlation"


# --- telemetry: opt-in configuration --------------------------------------


def test_metrics_are_safe_without_an_exporter() -> None:
    configuration = telemetry_configuration()
    assert configuration.enabled is False
    assert configuration.exporter_configured is False
    record_api_request(method="GET", route="/api/v1/health", status_code=200, duration_seconds=0.001)
    record_error("TERMINAL_FAILURE")
    record_authorization_decision("read", allowed=True)
    assert record_db_pool_usage(size=1, checked_in=1, checked_out=0, overflow=0)["size"] == 1


def test_exporter_is_only_configured_when_an_endpoint_is_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TELEMETRY_ENABLED_ENV, raising=False)
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, "http://127.0.0.1:4318")
    configuration = configure_telemetry()
    assert configuration.enabled is False
    assert configuration.exporter_configured is True

    monkeypatch.delenv(OTLP_ENDPOINT_ENV, raising=False)
    without_endpoint = configure_telemetry()
    assert without_endpoint.exporter_configured is False


def test_repeated_identical_configuration_is_idempotent() -> None:
    meter = _RecordingMeter()
    first = configure_telemetry(meter=meter)
    created_after_first = list(meter.created)
    second = configure_telemetry(meter=meter)
    assert second == first
    assert meter.created == created_after_first


@pytest.mark.skipif(not otel_available(), reason="opentelemetry-api is not installed")
def test_enabling_telemetry_without_an_endpoint_builds_no_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TELEMETRY_ENABLED_ENV, "1")
    monkeypatch.delenv(OTLP_ENDPOINT_ENV, raising=False)
    configuration = configure_telemetry()
    assert configuration.enabled is True
    assert configuration.exporter_configured is False
    assert configuration.meter_source == "sdk"
    assert configuration.tracer_source == "sdk"
    record_api_request(method="GET", route="/api/v1/health", status_code=200, duration_seconds=0.0)
