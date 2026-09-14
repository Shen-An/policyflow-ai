"""Append-only audit sink implementing the ``AuditSink`` internal contract.

Contract summary (``specs/001-enterprise-agent-refactor/contracts/internal-contracts.md``)::

    AuditSink.append(event_id, tenant_id, run_id, actor_ref, action, resource_ref,
                     authorization_version, outcome, reason_code, redacted_metadata)

* Append is idempotent by ``event_id``. Appending the same ``event_id`` twice
  creates no second row and does not raise; the first write wins so the log stays
  append-only and no row is ever rewritten.
* Credentials, host file paths, raw provider payloads and unrestricted file
  bodies are forbidden. They are stripped or rejected *before* persistence, and
  every strip is recorded in the row so a redaction is never silent.
* Audit failure on approval or an external side effect blocks completion and
  leaves the operation recoverable; it is never silently ignored. The sink
  therefore never swallows a store failure: it raises
  :class:`AuditSinkUnavailableError`, which carries ``blocks_completion`` so the
  caller can stop the workflow without lying about its state.

Failure semantics
-----------------

``GuardedAuditSink.append`` has exactly two failure modes:

1. :class:`AuditContentRejectedError` — the supplied event cannot be persisted
   as given (unsupported value type, oversized or non-mapping metadata, reserved
   key, host path inside a reference). Nothing is written, and the caller must
   fix the payload; this is a programming error, not a transient condition.
2. :class:`AuditSinkUnavailableError` — the store could not persist the event
   (or reported a duplicate it cannot read back). Nothing is written. When the
   event is critical (approval or external side effect, see
   :func:`is_critical_action`) the error states ``blocks_completion=True``: the
   operation must not be reported as complete and stays recoverable, and the
   caller must retry the append before the side effect is acknowledged.

Storage boundary
----------------

Persistence is a narrow protocol (:class:`AuditPersistence`) with two
implementations: :class:`InMemoryAuditPersistence` for tests and
:class:`SqlAlchemyAuditPersistence` for the durable store. The ORM implementation
imports the ``AuditEvent`` model *inside* the method body, which keeps this module
importable and testable independently of the model's landing order: with the model
present in ``backend/app/db/models.py`` (task T026) the ORM path persists for
real, and when it is absent the call raises :class:`AuditSinkUnavailableError` with
:attr:`AuditFailureReason.MODEL_UNAVAILABLE` instead of pretending to persist.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from backend.app.core.redaction import REDACTED_VALUE, is_sensitive_key, redact_sensitive
from backend.app.observability.errors import ContractError, ErrorCode
from backend.app.observability.guards import (
    ACTION_PATTERN,
    CONTROL_CHARACTER_PATTERN,
    MAX_METADATA_BYTES,
    MAX_METADATA_DEPTH,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_STRING_LENGTH,
    MAX_REASON_CODE_LENGTH,
    MAX_REFERENCE_LENGTH,
    METADATA_KEY_PATTERN,
    PATH_MARKER,
    PROVIDER_PAYLOAD_MARKER,
    REASON_CODE_PATTERN,
    UUID_PATTERN,
    credential_kind,
    host_path_kind,
    looks_like_binary_blob,
    looks_like_provider_payload,
    strip_credentials,
    strip_host_paths,
)
from backend.app.observability.telemetry import CorrelationContext

BODY_MARKER_TEMPLATE = "[REDACTED_BODY:{length}]"
REDACTION_REPORT_KEY = "_redaction"

# Actions whose audit failure must block completion and leave the operation
# recoverable: approvals and external side effects.
CRITICAL_ACTION_PREFIXES = (
    "approval",
    "approve",
    "connector",
    "submit",
    "publish",
    "delete",
    "external",
    "sandbox",
)


def new_event_id() -> str:
    """Return a fresh UUID string for a new audit event."""
    return str(uuid4())


class AuditOutcome(StrEnum):
    """Allowed audit outcomes."""

    ALLOWED = "allowed"
    DENIED = "denied"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    RECOVERABLE_FAILURE = "recoverable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    UNKNOWN_OUTCOME = "unknown_outcome"


class AuditFailureReason(StrEnum):
    """Why an audit append could not be persisted."""

    STORE_ERROR = "store_error"
    STORE_UNAVAILABLE = "store_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    DUPLICATE_NOT_READABLE = "duplicate_not_readable"


def is_critical_action(action: str) -> bool:
    """Return True when an audit failure for ``action`` must block completion."""
    return action.split(".", 1)[0] in CRITICAL_ACTION_PREFIXES


class AuditContentRejectedError(ContractError):
    """The audit event cannot be persisted as given.

    ``violations`` names the kinds of violation only; the offending values are
    never stored on the error, so a rejection cannot leak the secret, path or
    payload that triggered it.
    """

    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = tuple(dict.fromkeys(violations))
        super().__init__(
            ErrorCode.AUDIT_CONTENT_REJECTED,
            details={"violations": list(self.violations)},
        )


class AuditSinkUnavailableError(ContractError):
    """The audit event could not be persisted; the operation must not be completed.

    The sink raises this instead of logging and continuing, because an audit
    failure on approval or an external side effect must block completion and
    leave the operation recoverable. ``blocks_completion`` states whether this
    particular event is critical, and ``recoverable`` is always True: nothing was
    written, so retrying the append is safe and cannot duplicate a row.
    """

    def __init__(
        self,
        *,
        action: str,
        reason: AuditFailureReason,
        blocks_completion: bool,
        detail: str | None = None,
    ) -> None:
        self.action = action
        self.reason = reason
        self.blocks_completion = bool(blocks_completion)
        self.recoverable = True
        prefix = (
            "Audit append failed for a critical action; the operation must not be "
            "reported as complete and remains recoverable"
            if self.blocks_completion
            else "Audit append failed"
        )
        message = f"{prefix} ({action}, {reason})"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(ErrorCode.AUDIT_UNAVAILABLE, message=message, retryable=True)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One append-only audit row.

    ``redacted_metadata`` has already passed the sanitizer, and
    ``redactions`` lists what was stripped so a reviewer can see that a redaction
    happened without seeing the removed value.
    """

    event_id: str
    tenant_id: str
    run_id: str | None
    actor_ref: str
    action: str
    resource_ref: str
    authorization_version: int
    outcome: str
    reason_code: str | None
    redacted_metadata: Mapping[str, Any]
    recorded_at: datetime
    request_id: str | None = None
    trace_id: str | None = None
    redactions: tuple[str, ...] = ()


@dataclass
class _SanitizeState:
    """Mutable bookkeeping for one metadata sanitization pass."""

    entries: int = 0
    redactions: list[str] = field(default_factory=list)

    def reject(self, violation: str, message: str) -> None:
        raise AuditContentRejectedError([violation, message])


@runtime_checkable
class AuditPersistence(Protocol):
    """Narrow storage boundary for audit rows.

    Implementations must guarantee at-most-one row per ``event_id``: duplicate
    inserts return False instead of raising or writing a second row, and reads
    never mutate stored rows.
    """

    async def insert(self, record: AuditRecord) -> bool:
        """Persist ``record``; return False when ``event_id`` already exists."""
        ...

    async def get(self, event_id: str) -> AuditRecord | None:
        """Return the stored row for ``event_id``, or None."""
        ...

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditRecord]:
        """Return rows for one tenant, oldest first, optionally filtered by run."""
        ...


class InMemoryAuditPersistence:
    """In-memory :class:`AuditPersistence` for tests and local wiring."""

    def __init__(self) -> None:
        self._records: dict[str, AuditRecord] = {}
        self._order: list[str] = []

    async def insert(self, record: AuditRecord) -> bool:
        if record.event_id in self._records:
            return False
        self._records[record.event_id] = _copy_record(record)
        self._order.append(record.event_id)
        return True

    async def get(self, event_id: str) -> AuditRecord | None:
        stored = self._records.get(event_id)
        return None if stored is None else _copy_record(stored)

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditRecord]:
        rows = [
            _copy_record(self._records[event_id])
            for event_id in self._order
            if self._records[event_id].tenant_id == tenant_id
            and (run_id is None or self._records[event_id].run_id == run_id)
        ]
        return rows[:limit]

    @property
    def rows(self) -> tuple[AuditRecord, ...]:
        """Return an immutable view of every row in insertion order."""
        return tuple(_copy_record(self._records[event_id]) for event_id in self._order)

    def __len__(self) -> int:
        return len(self._order)


class SqlAlchemyAuditPersistence:
    """Durable :class:`AuditPersistence` backed by an async SQLAlchemy session.

    The ``AuditEvent`` model is imported lazily inside each method, so this module
    imports cleanly before the model exists; the ORM implementation activates once
    the ``AuditEvent`` model lands in ``backend/app/db/models.py``. Until then a
    call raises :class:`AuditSinkUnavailableError`.

    Idempotency uses the dialect's insert-on-conflict on the ``event_id`` unique
    index (PostgreSQL and SQLite), and falls back to a ``SAVEPOINT``-scoped insert
    plus an ``IntegrityError`` catch on any other dialect, so a concurrent
    duplicate append cannot produce a second row or break the caller's unit of
    work.

    The record is mapped onto the model's own columns: a direct ``resource_ref``
    column is used when present, otherwise the reference is split into
    ``resource_kind``/``resource_id``, and ``occurred_at`` or ``created_at``
    supplies the timestamp. A field that cannot fit its column is reported as
    rejected content instead of being truncated or turned into a blocking store
    failure.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def insert(self, record: AuditRecord) -> bool:
        from sqlalchemy.exc import IntegrityError

        model = _audit_event_model()
        values = _column_values(model, record)
        statement = _insert_on_conflict_statement(model, values, self._session)
        try:
            if statement is None:
                async with self._session.begin_nested():
                    result = await self._session.execute(_plain_insert(model, values))
            else:
                result = await self._session.execute(statement)
        except IntegrityError:
            # Another writer inserted the same event_id first: the row exists once.
            return False
        return bool(getattr(result, "rowcount", 1))

    async def get(self, event_id: str) -> AuditRecord | None:
        from sqlalchemy import select

        model = _audit_event_model()
        statement = select(model).where(_event_id_column(model) == event_id).limit(1)
        result = await self._session.execute(statement)
        row = result.scalars().first()
        return None if row is None else _record_from_row(row)

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditRecord]:
        from sqlalchemy import select

        model = _audit_event_model()
        statement = select(model).where(getattr(model, "tenant_id") == tenant_id)
        if run_id is not None:
            statement = statement.where(getattr(model, "run_id") == run_id)
        order_column = (
            getattr(model, "occurred_at", None)
            or getattr(model, "created_at", None)
            or _event_id_column(model)
        )
        statement = statement.order_by(order_column).limit(limit)
        result = await self._session.execute(statement)
        return tuple(_record_from_row(row) for row in result.scalars().all())


@runtime_checkable
class AuditSink(Protocol):
    """The ``AuditSink`` contract: validate, redact and append exactly once."""

    async def append(
        self,
        event_id: str,
        tenant_id: str,
        run_id: str | None,
        actor_ref: str,
        action: str,
        resource_ref: str,
        authorization_version: int,
        outcome: str,
        reason_code: str | None,
        redacted_metadata: Mapping[str, Any] | None = None,
    ) -> AuditRecord:
        """Append one audit event and return the stored row."""
        ...


class GuardedAuditSink:
    """Redacting, validating :class:`AuditSink` on top of an :class:`AuditPersistence`.

    See the module docstring for the failure semantics.
    """

    def __init__(self, persistence: AuditPersistence) -> None:
        self._persistence = persistence

    @property
    def persistence(self) -> AuditPersistence:
        """Return the storage boundary this sink writes to."""
        return self._persistence

    async def append(
        self,
        event_id: str,
        tenant_id: str,
        run_id: str | None,
        actor_ref: str,
        action: str,
        resource_ref: str,
        authorization_version: int,
        outcome: str,
        reason_code: str | None,
        redacted_metadata: Mapping[str, Any] | None = None,
        *,
        critical: bool | None = None,
    ) -> AuditRecord:
        """Validate, redact and persist one event, exactly once per ``event_id``.

        ``critical`` overrides the classification derived from ``action``; it only
        affects the ``blocks_completion`` flag of a raised
        :class:`AuditSinkUnavailableError` and never the stored row.
        """
        correlation = _current_correlation()
        record = build_audit_record(
            event_id=event_id,
            tenant_id=tenant_id,
            run_id=run_id,
            actor_ref=actor_ref,
            action=action,
            resource_ref=resource_ref,
            authorization_version=authorization_version,
            outcome=outcome,
            reason_code=reason_code,
            redacted_metadata=redacted_metadata,
            correlation=correlation,
        )
        blocks_completion = is_critical_action(action) if critical is None else bool(critical)
        try:
            inserted = await self._persistence.insert(record)
        except AuditSinkUnavailableError:
            raise
        except Exception as exc:
            raise AuditSinkUnavailableError(
                action=action,
                reason=AuditFailureReason.STORE_ERROR,
                blocks_completion=blocks_completion,
                detail=type(exc).__name__,
            ) from exc
        if inserted:
            return record
        return await self._read_back_duplicate(event_id, action, blocks_completion)

    async def _read_back_duplicate(
        self,
        event_id: str,
        action: str,
        blocks_completion: bool,
    ) -> AuditRecord:
        """Return the row that already owns ``event_id``.

        The first write wins and no row is ever rewritten, so a duplicate append
        reports the stored row. A store that claims a duplicate but cannot return
        it is reported as an unavailable sink rather than as a fabricated success.
        """
        try:
            existing = await self._persistence.get(event_id)
        except Exception as exc:
            raise AuditSinkUnavailableError(
                action=action,
                reason=AuditFailureReason.DUPLICATE_NOT_READABLE,
                blocks_completion=blocks_completion,
                detail=type(exc).__name__,
            ) from exc
        if existing is None:
            raise AuditSinkUnavailableError(
                action=action,
                reason=AuditFailureReason.DUPLICATE_NOT_READABLE,
                blocks_completion=blocks_completion,
                detail="duplicate event_id reported but the stored row is unreadable",
            )
        return existing


def in_memory_audit_sink() -> GuardedAuditSink:
    """Return an audit sink backed by an in-memory store (tests and local wiring)."""
    return GuardedAuditSink(InMemoryAuditPersistence())


def sqlalchemy_audit_sink(session: Any) -> GuardedAuditSink:
    """Return an audit sink persisting through one async SQLAlchemy session."""
    return GuardedAuditSink(SqlAlchemyAuditPersistence(session))


def build_audit_record(
    *,
    event_id: str,
    tenant_id: str,
    run_id: str | None,
    actor_ref: str,
    action: str,
    resource_ref: str,
    authorization_version: int,
    outcome: str,
    reason_code: str | None,
    redacted_metadata: Mapping[str, Any] | None,
    correlation: CorrelationContext | None = None,
) -> AuditRecord:
    """Validate and redact one event into an immutable :class:`AuditRecord`.

    ``run_id`` falls back to the bound telemetry correlation context when the
    caller passes None, so an audit row can always be correlated with the run,
    request and trace that produced it.
    """
    violations: list[str] = []
    resolved_event_id = _uuid_field(event_id, "event_id", violations)
    resolved_tenant_id = _uuid_field(tenant_id, "tenant_id", violations)
    resolved_run_id = (
        _uuid_field(run_id, "run_id", violations)
        if run_id is not None
        else (correlation.run_id if correlation is not None else None)
    )
    resolved_actor_ref = _reference_field(actor_ref, "actor_ref", violations)
    resolved_resource_ref = _reference_field(resource_ref, "resource_ref", violations)
    resolved_action = _action_field(action, violations)
    resolved_outcome = _outcome_field(outcome, violations)
    resolved_reason_code = _reason_code_field(reason_code, violations)
    resolved_version = _authorization_version_field(authorization_version, violations)

    if violations:
        raise AuditContentRejectedError(violations)

    sanitized, redactions = sanitize_audit_metadata(redacted_metadata)
    return AuditRecord(
        event_id=resolved_event_id,
        tenant_id=resolved_tenant_id,
        run_id=resolved_run_id,
        actor_ref=resolved_actor_ref,
        action=resolved_action,
        resource_ref=resolved_resource_ref,
        authorization_version=resolved_version,
        outcome=str(resolved_outcome),
        reason_code=resolved_reason_code,
        redacted_metadata=sanitized,
        recorded_at=datetime.now(UTC),
        request_id=correlation.request_id if correlation is not None else None,
        trace_id=correlation.trace_id if correlation is not None else None,
        redactions=redactions,
    )


def sanitize_audit_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return audit-safe metadata plus the list of redaction kinds applied.

    Credentials are redacted, host paths are replaced, raw provider payloads are
    collapsed to a marker and oversized/binary bodies are handled per value. The
    structural violations that cannot be summarised honestly - a non-mapping
    metadata object, unsupported value types (including open file objects and byte
    blobs), reserved or malformed keys, excessive depth, entry count or total size
    - are rejected with :class:`AuditContentRejectedError` before persistence.
    """
    if metadata is None:
        return {}, ()
    if not isinstance(metadata, Mapping):
        raise AuditContentRejectedError(["metadata_not_mapping"])
    state = _SanitizeState()
    # Key shapes are validated against the caller's own mapping first, because the
    # shared redaction policy stringifies keys and would hide a non-string key.
    _assert_metadata_keys(metadata)
    # Key-based redaction is the shared policy from backend.app.core.redaction:
    # redact_sensitive replaces every value whose key names a credential. It runs
    # before the structural pass so a secret can never reach it.
    pre_redacted = redact_sensitive(dict(metadata))
    if _has_sensitive_key(metadata):
        state.redactions.append("credential:key")
    cleaned = _sanitize_value(pre_redacted, depth=0, state=state)
    if len(json.dumps(cleaned, ensure_ascii=False, default=str).encode("utf-8")) > (
        MAX_METADATA_BYTES
    ):
        raise AuditContentRejectedError(["metadata_too_large"])
    redactions = tuple(dict.fromkeys(state.redactions))
    if redactions:
        cleaned[REDACTION_REPORT_KEY] = list(redactions)
    return cleaned, redactions


def _assert_metadata_keys(value: Any) -> None:
    """Reject malformed, reserved or non-string metadata keys before redaction."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            violation = _metadata_key_violation(key)
            if violation is not None:
                raise AuditContentRejectedError([violation])
            _assert_metadata_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_metadata_keys(item)


def _has_sensitive_key(value: Any) -> bool:
    """Return True when the metadata tree holds a key named like a credential."""
    if isinstance(value, Mapping):
        return any(
            is_sensitive_key(str(key)) or _has_sensitive_key(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_sensitive_key(item) for item in value)
    return False


def _sanitize_value(value: Any, *, depth: int, state: _SanitizeState) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_string(value, state)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise AuditContentRejectedError(["file_body:binary_payload"])
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth=depth, state=state)
    if isinstance(value, (list, tuple)):
        _count_entry(state)
        cleaned_items = []
        for item in value:
            _count_entry(state)
            cleaned_items.append(_sanitize_value(item, depth=depth + 1, state=state))
        return cleaned_items
    raise AuditContentRejectedError([f"unsupported_value_type:{type(value).__name__}"])


def _count_entry(state: _SanitizeState) -> None:
    """Count one metadata node and enforce the entry limit."""
    state.entries += 1
    if state.entries > MAX_METADATA_ENTRIES:
        raise AuditContentRejectedError(["metadata_too_many_entries"])


def _sanitize_string(value: str, state: _SanitizeState) -> str:
    if len(value) > MAX_METADATA_STRING_LENGTH or looks_like_binary_blob(value):
        state.redactions.append("file_body")
        return BODY_MARKER_TEMPLATE.format(length=len(value))
    if _looks_like_embedded_provider_payload(value):
        state.redactions.append("provider_payload")
        return PROVIDER_PAYLOAD_MARKER
    cleaned = value
    credential = credential_kind(cleaned)
    if credential is not None:
        state.redactions.append(f"credential:{credential}")
        cleaned = strip_credentials(cleaned, REDACTED_VALUE)
    path = host_path_kind(cleaned)
    if path is not None:
        state.redactions.append(f"host_path:{path}")
        cleaned = strip_host_paths(cleaned, PATH_MARKER)
    return cleaned


def _sanitize_mapping(
    value: Mapping[Any, Any],
    *,
    depth: int,
    state: _SanitizeState,
) -> dict[str, Any]:
    state.entries += 1
    if state.entries > MAX_METADATA_ENTRIES:
        raise AuditContentRejectedError(["metadata_too_many_entries"])
    if depth >= MAX_METADATA_DEPTH:
        raise AuditContentRejectedError(["metadata_too_deep"])
    if looks_like_provider_payload(value):
        state.redactions.append("provider_payload")
        return {PROVIDER_PAYLOAD_MARKER: True}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_violation = _metadata_key_violation(key)
        if key_violation is not None:
            raise AuditContentRejectedError([key_violation])
        _count_entry(state)
        cleaned[str(key)] = _sanitize_value(item, depth=depth + 1, state=state)
    return cleaned


def _metadata_key_violation(key: Any) -> str | None:
    if not isinstance(key, str):
        return "metadata_key_not_string"
    if key.startswith("_"):
        return "metadata_key_reserved"
    if not METADATA_KEY_PATTERN.match(key):
        return "metadata_key_malformed"
    return None


def _looks_like_embedded_provider_payload(value: str) -> bool:
    probe = value.strip()
    if not probe.startswith("{") or len(probe) > MAX_METADATA_STRING_LENGTH:
        return False
    try:
        decoded = json.loads(probe)
    except ValueError:
        return False
    return isinstance(decoded, Mapping) and looks_like_provider_payload(decoded)


def _uuid_field(value: Any, field_name: str, violations: list[str]) -> str:
    if not isinstance(value, str) or not UUID_PATTERN.match(value):
        violations.append(f"{field_name}_not_uuid")
        return "" if not isinstance(value, str) else value
    return value


def _reference_field(value: Any, field_name: str, violations: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        violations.append(f"{field_name}_empty")
        return "" if not isinstance(value, str) else value
    if len(value) > MAX_REFERENCE_LENGTH:
        violations.append(f"{field_name}_too_long")
    if CONTROL_CHARACTER_PATTERN.search(value):
        violations.append(f"{field_name}_control_characters")
    if credential_kind(value) is not None:
        violations.append(f"{field_name}_credential")
    if host_path_kind(value) is not None:
        violations.append(f"{field_name}_host_path")
    return value


def _action_field(value: Any, violations: list[str]) -> str:
    if not isinstance(value, str) or not ACTION_PATTERN.match(value):
        violations.append("action_malformed")
        return "" if not isinstance(value, str) else value
    return value


def _outcome_field(value: Any, violations: list[str]) -> AuditOutcome:
    try:
        return AuditOutcome(value)
    except ValueError:
        violations.append("outcome_unknown")
        return AuditOutcome.TERMINAL_FAILURE


def _reason_code_field(value: Any, violations: list[str]) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > MAX_REASON_CODE_LENGTH
        or not REASON_CODE_PATTERN.match(value)
    ):
        violations.append("reason_code_malformed")
        return None
    return value


def _authorization_version_field(value: Any, violations: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        violations.append("authorization_version_invalid")
        return 0
    return value


def _current_correlation() -> CorrelationContext | None:
    from backend.app.observability.telemetry import get_correlation

    try:
        return get_correlation()
    except Exception:  # pragma: no cover - correlation must never block an audit
        return None


def _copy_record(record: AuditRecord) -> AuditRecord:
    return AuditRecord(
        event_id=record.event_id,
        tenant_id=record.tenant_id,
        run_id=record.run_id,
        actor_ref=record.actor_ref,
        action=record.action,
        resource_ref=record.resource_ref,
        authorization_version=record.authorization_version,
        outcome=record.outcome,
        reason_code=record.reason_code,
        redacted_metadata=json.loads(json.dumps(record.redacted_metadata, default=str)),
        recorded_at=record.recorded_at,
        request_id=record.request_id,
        trace_id=record.trace_id,
        redactions=record.redactions,
    )


def _audit_event_model() -> Any:
    """Import the ``AuditEvent`` model lazily, inside the method that needs it.

    The lazy import keeps this module importable whether or not the model exists
    yet, and makes the activation point explicit: the ORM persistence becomes live
    as soon as ``AuditEvent`` is present in ``backend/app/db/models.py`` (task T026
    added it), and reports MODEL_UNAVAILABLE rather than pretending to persist when
    it is missing.
    """
    try:
        from backend.app.db.models import AuditEvent
    except ImportError as exc:
        raise AuditSinkUnavailableError(
            action="audit.append",
            reason=AuditFailureReason.MODEL_UNAVAILABLE,
            blocks_completion=True,
            detail="AuditEvent model is not present yet (task T026)",
        ) from exc
    return AuditEvent


def _event_id_column(model: Any) -> Any:
    column = getattr(model, "event_id", None)
    if column is None:
        raise AuditSinkUnavailableError(
            action="audit.append",
            reason=AuditFailureReason.MODEL_UNAVAILABLE,
            blocks_completion=True,
            detail="AuditEvent model has no event_id column",
        )
    return column


def _column_values(model: Any, record: AuditRecord) -> dict[str, Any]:
    """Map an audit record onto the model's own columns.

    The ``AuditEvent`` model may store the resource reference directly
    (``resource_ref``) or as the split ``resource_kind``/``resource_id`` pair, and
    its timestamp may be ``occurred_at`` or ``created_at``; whichever columns exist
    are used. A value that cannot fit its column is rejected as bad content rather
    than being truncated or reported as a blocking store failure.
    """
    columns = {column.key: column for column in model.__table__.columns}
    resource_kind, resource_id = _split_resource_ref(record.resource_ref)
    candidates: dict[str, Any] = {
        "event_id": record.event_id,
        "tenant_id": record.tenant_id,
        "run_id": record.run_id,
        "request_id": record.request_id,
        "trace_id": record.trace_id,
        "actor_ref": record.actor_ref,
        "authorization_version": record.authorization_version,
        "action": record.action,
        "outcome": record.outcome,
        "reason_code": record.reason_code,
        "redacted_metadata": dict(record.redacted_metadata),
        "resource_ref": record.resource_ref,
        "resource_kind": resource_kind,
        "resource_id": resource_id,
    }
    for timestamp_column in ("occurred_at", "created_at"):
        candidates[timestamp_column] = record.recorded_at

    values: dict[str, Any] = {}
    for key, value in candidates.items():
        column = columns.get(key)
        if column is None:
            continue
        _assert_column_fits(key, value, column)
        values[key] = value
    if "event_id" not in values or "tenant_id" not in values:
        raise AuditSinkUnavailableError(
            action="audit.append",
            reason=AuditFailureReason.MODEL_UNAVAILABLE,
            blocks_completion=True,
            detail="AuditEvent model is missing a required column",
        )
    if not {"resource_ref", "resource_kind", "resource_id"} & set(values):
        raise AuditSinkUnavailableError(
            action="audit.append",
            reason=AuditFailureReason.MODEL_UNAVAILABLE,
            blocks_completion=True,
            detail="AuditEvent model cannot store the resource reference",
        )
    return values


def _split_resource_ref(resource_ref: str) -> tuple[str, str | None]:
    """Split ``kind:id`` (or ``kind/path``) into the model's kind and identifier."""
    for separator in (":", "/"):
        kind, found, remainder = resource_ref.partition(separator)
        if found and kind and remainder:
            return kind, remainder
    return resource_ref, None


def _assert_column_fits(key: str, value: Any, column: Any) -> None:
    """Reject a value that is wider than its column instead of truncating it."""
    if not isinstance(value, str):
        return
    limit = getattr(column.type, "length", None)
    if isinstance(limit, int) and len(value) > limit:
        raise AuditContentRejectedError([f"{key}_exceeds_column_length"])


def _plain_insert(model: Any, values: Mapping[str, Any]) -> Any:
    from sqlalchemy import insert

    return insert(model).values(**values)


def _insert_on_conflict_statement(
    model: Any,
    values: Mapping[str, Any],
    session: Any,
) -> Any | None:
    """Return an insert-on-conflict statement for the session dialect, or None."""
    dialect = _dialect_name(session)
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return (
            postgresql_insert(model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return (
            sqlite_insert(model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
    return None


def _dialect_name(session: Any) -> str:
    try:
        return str(session.get_bind().dialect.name)
    except Exception:  # pragma: no cover - unknown bind falls back to plain insert
        return ""


def _record_from_row(row: Any) -> AuditRecord:
    metadata = getattr(row, "redacted_metadata", None)
    resource_ref = getattr(row, "resource_ref", None)
    if resource_ref is None:
        kind = getattr(row, "resource_kind", None) or ""
        identifier = getattr(row, "resource_id", None)
        resource_ref = f"{kind}:{identifier}" if identifier else kind
    recorded_at = (
        getattr(row, "occurred_at", None)
        or getattr(row, "created_at", None)
        or datetime.now(UTC)
    )
    return AuditRecord(
        event_id=str(getattr(row, "event_id", "")),
        tenant_id=str(getattr(row, "tenant_id", "")),
        run_id=getattr(row, "run_id", None),
        actor_ref=str(getattr(row, "actor_ref", "")),
        action=str(getattr(row, "action", "")),
        resource_ref=str(resource_ref),
        authorization_version=int(getattr(row, "authorization_version", 0) or 0),
        outcome=str(getattr(row, "outcome", "")),
        reason_code=getattr(row, "reason_code", None),
        redacted_metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        recorded_at=recorded_at,
        request_id=getattr(row, "request_id", None),
        trace_id=getattr(row, "trace_id", None),
    )
