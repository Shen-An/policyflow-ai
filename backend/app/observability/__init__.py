"""Observability boundaries: audit sink, telemetry and the stable error contract.

* :mod:`backend.app.observability.audit` - append-only ``AuditSink`` with strict
  redaction, idempotent append by ``event_id`` and blocking failure semantics.
* :mod:`backend.app.observability.telemetry` - ``run_id``/``request_id``/
  ``trace_id`` correlation plus API, DB pool, error and authorization metrics
  that never use tenant or user identifiers as label values.
* :mod:`backend.app.observability.errors` - the stable boundary error vocabulary
  with retryability, server-controlled retry delay and non-enumerating not-found.
* :mod:`backend.app.observability.guards` - shared forbidden-content guards for
  credentials, host paths, raw provider payloads and file bodies.

Importing this package creates no provider, exporter, socket or thread.
"""

from __future__ import annotations

from backend.app.observability.audit import (
    CRITICAL_ACTION_PREFIXES,
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
    in_memory_audit_sink,
    is_critical_action,
    new_event_id,
    sanitize_audit_metadata,
    sqlalchemy_audit_sink,
)
from backend.app.observability.errors import (
    ERROR_SEMANTICS,
    MAX_RETRY_AFTER_SECONDS,
    ContractError,
    ErrorCode,
    ErrorSemantics,
    ResourceVisibility,
    resolve_resource_visibility,
    resource_not_found,
    semantics_for,
)
from backend.app.observability.telemetry import (
    CorrelationContext,
    TelemetryConfiguration,
    TelemetryPolicyError,
    annotate_current_span,
    bind_correlation,
    configure_telemetry,
    correlation_attributes,
    correlation_context,
    current_request_id,
    current_run_id,
    current_trace_id,
    get_correlation,
    otel_available,
    record_api_request,
    record_authorization_decision,
    record_db_pool_usage,
    record_error,
    reset_correlation,
    reset_telemetry,
    span,
    telemetry_configuration,
    validate_metric_labels,
    validate_span_attributes,
)

__all__ = [
    "CRITICAL_ACTION_PREFIXES",
    "ERROR_SEMANTICS",
    "MAX_RETRY_AFTER_SECONDS",
    "AuditContentRejectedError",
    "AuditFailureReason",
    "AuditOutcome",
    "AuditPersistence",
    "AuditRecord",
    "AuditSink",
    "AuditSinkUnavailableError",
    "ContractError",
    "CorrelationContext",
    "ErrorCode",
    "ErrorSemantics",
    "GuardedAuditSink",
    "InMemoryAuditPersistence",
    "ResourceVisibility",
    "SqlAlchemyAuditPersistence",
    "TelemetryConfiguration",
    "TelemetryPolicyError",
    "annotate_current_span",
    "bind_correlation",
    "build_audit_record",
    "configure_telemetry",
    "correlation_attributes",
    "correlation_context",
    "current_request_id",
    "current_run_id",
    "current_trace_id",
    "get_correlation",
    "in_memory_audit_sink",
    "is_critical_action",
    "new_event_id",
    "otel_available",
    "record_api_request",
    "record_authorization_decision",
    "record_db_pool_usage",
    "record_error",
    "reset_correlation",
    "reset_telemetry",
    "resource_not_found",
    "resolve_resource_visibility",
    "sanitize_audit_metadata",
    "semantics_for",
    "span",
    "sqlalchemy_audit_sink",
    "telemetry_configuration",
    "validate_metric_labels",
    "validate_span_attributes",
]
