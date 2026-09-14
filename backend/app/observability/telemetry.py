"""OpenTelemetry correlation, metrics and the metric-label policy.

Three responsibilities:

1. **Correlation.** ``run_id``, ``request_id`` and ``trace_id`` are bound in
   context variables so logs, spans, audit rows and error records produced inside
   one API request or worker attempt describe the same run.
2. **Metrics.** API requests (count and duration), DB pool usage, errors and
   authorization decisions are recorded through helpers that validate every label
   before the instrument sees it.
3. **Label policy.** Tenant and user identifiers are *never* metric label values:
   they are unbounded and personally identifying, so they would explode
   Prometheus cardinality and spread personal data into metrics storage. The
   policy is enforced in code - every label key and value passing through this
   module is validated, and an identifier-shaped key or value is rejected with
   :class:`TelemetryPolicyError`. Trace and log attributes may carry tenant/run
   identifiers under retention controls, which is why
   :func:`correlation_attributes` accepts a tenant id while
   :func:`validate_metric_labels` does not.

Opt-in and safe by default: importing this module creates no provider, no
exporter, no socket and no thread. Instrument handles are created lazily from the
OpenTelemetry API proxies, so recording a measurement without a configured
exporter is a no-op. :func:`configure_telemetry` is the only place that builds
SDK providers, and it adds an OTLP exporter only when an endpoint is supplied
explicitly or through ``OTEL_EXPORTER_OTLP_ENDPOINT``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from backend.app.core.logging import get_request_id as _logging_request_id
from backend.app.observability.errors import ErrorCode, semantics_for
from backend.app.observability.guards import (
    ACTION_PATTERN,
    CONTROL_CHARACTER_PATTERN,
    MAX_METRIC_LABEL_VALUE_LENGTH,
    REASON_CODE_PATTERN,
    TRACE_ID_PATTERN,
    UUID_PATTERN,
    contains_identifier,
    credential_kind,
    host_path_kind,
)

try:  # pragma: no cover - opentelemetry-api is a declared dependency
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import ProxyTracerProvider as _ProxyTracerProvider
except ImportError:  # pragma: no cover - degraded mode for minimal installs
    _otel_metrics = None
    _otel_trace = None
    _ProxyTracerProvider = None

OTEL_AVAILABLE = _otel_metrics is not None and _otel_trace is not None

SERVICE_NAME = "policyflow"
TELEMETRY_ENABLED_ENV = "POLICYFLOW_TELEMETRY_ENABLED"
OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})

METRIC_API_REQUESTS = "policyflow.api.requests"
METRIC_API_DURATION = "policyflow.api.request.duration"
METRIC_DB_POOL_CONNECTIONS = "policyflow.db.pool.connections"
METRIC_DB_POOL_SIZE = "policyflow.db.pool.size"
METRIC_ERRORS = "policyflow.errors"
METRIC_AUTHORIZATION_DECISIONS = "policyflow.authorization.decisions"

ATTR_RUN_ID = "policyflow.run_id"
ATTR_REQUEST_ID = "policyflow.request_id"
ATTR_TRACE_ID = "policyflow.trace_id"
ATTR_TENANT_ID = "policyflow.tenant_id"
ATTR_ACTOR_REF = "policyflow.actor_ref"
ATTR_HTTP_METHOD = "http.request.method"
ATTR_HTTP_ROUTE = "http.route"
ATTR_HTTP_STATUS = "http.response.status_code"
ATTR_ERROR_CODE = "policyflow.error.code"
ATTR_ERROR_RETRYABLE = "policyflow.error.retryable"
ATTR_ERROR_BOUNDARY = "policyflow.error.boundary"
ATTR_AUTH_ACTION = "policyflow.authorization.action"
ATTR_AUTH_DECISION = "policyflow.authorization.decision"
ATTR_DB_POOL = "db.pool.name"
ATTR_DB_POOL_SIZE = "db.pool.size"
ATTR_DB_POOL_CHECKED_IN = "db.pool.checked_in"
ATTR_DB_POOL_CHECKED_OUT = "db.pool.checked_out"
ATTR_DB_POOL_OVERFLOW = "db.pool.overflow"

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})
DB_POOL_STATES = ("checked_in", "checked_out", "overflow")

# Key shapes that always name an identifier, never a bounded label.
IDENTITY_KEY_SUFFIXES = (
    "_id",
    "_ref",
    "_uuid",
    "_key",
    "_email",
    "_token",
    "_hash",
    "_digest",
    "_sha256",
    "_secret",
    "_password",
    "_credential",
    "_principal",
    "_subject",
)
IDENTITY_LABEL_KEYS = frozenset(
    {
        "id",
        "uuid",
        "tenant",
        "tenants",
        "user",
        "users",
        "actor",
        "email",
        "principal",
        "subject",
        "owner",
        "session",
        "membership",
        "run",
        "request",
        "trace",
        "span",
        "resource",
        "document",
        "conversation",
        "connection_string",
        "ip_address",
        "client_ip",
        "remote_addr",
    }
)
SPAN_ATTRIBUTE_MAX_VALUE_LENGTH = 512
SPAN_ATTRIBUTE_MAX_KEY_LENGTH = 128


class TelemetryPolicyError(ValueError):
    """A telemetry input violates the metric-label or attribute policy.

    ``scope`` names the rule family and ``violation`` the specific rule; the
    rejected value is never echoed, so the error cannot leak a tenant id, email
    or credential into logs.
    """

    def __init__(self, scope: str, violation: str, message: str) -> None:
        super().__init__(f"{scope}: {message}")
        self.scope = scope
        self.violation = violation


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Correlation identifiers for one request or worker attempt."""

    run_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class TelemetryConfiguration:
    """Effective telemetry configuration, safe to log or expose on health."""

    service_name: str
    enabled: bool
    otel_available: bool
    exporter_configured: bool
    meter_source: str
    tracer_source: str


_EMPTY_CORRELATION = CorrelationContext()
_CORRELATION: ContextVar[CorrelationContext] = ContextVar(
    "policyflow_correlation",
    default=_EMPTY_CORRELATION,
)

_STATE_LOCK = threading.RLock()
_INSTRUMENTS: Any = None
_POOL_SNAPSHOTS: dict[str, dict[str, int]] = {}


@dataclass
class _TelemetryState:
    """Module-level configuration, mutated only under ``_STATE_LOCK``."""

    service_name: str = SERVICE_NAME
    enabled: bool = False
    exporter_endpoint: str | None = None
    meter: Any = None
    tracer: Any = None
    meter_provider: Any = None
    tracer_provider: Any = None
    configuration_signature: tuple[Any, ...] | None = None


_STATE = _TelemetryState()


# --- correlation -----------------------------------------------------------


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not UUID_PATTERN.match(run_id):
        raise TelemetryPolicyError(
            "correlation", "run_id_not_uuid", "run_id must be a UUID string"
        )
    return run_id


def _validate_request_id(request_id: str) -> str:
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 128
        or CONTROL_CHARACTER_PATTERN.search(request_id)
    ):
        raise TelemetryPolicyError(
            "correlation", "request_id_malformed", "request_id must be a short printable string"
        )
    return request_id


def _validate_trace_id(trace_id: str) -> str:
    if not isinstance(trace_id, str) or not TRACE_ID_PATTERN.match(trace_id):
        raise TelemetryPolicyError(
            "correlation", "trace_id_malformed", "trace_id must be 32 lowercase hex characters"
        )
    return trace_id


def bind_correlation(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> Token[CorrelationContext]:
    """Bind the supplied identifiers onto the current correlation context.

    Only the identifiers that are not None are replaced; the others keep their
    current value. The returned token restores the previous context through
    :func:`reset_correlation`.
    """
    current = _CORRELATION.get()
    updated = CorrelationContext(
        run_id=_validate_run_id(run_id) if run_id is not None else current.run_id,
        request_id=(
            _validate_request_id(request_id) if request_id is not None else current.request_id
        ),
        trace_id=_validate_trace_id(trace_id) if trace_id is not None else current.trace_id,
    )
    return _CORRELATION.set(updated)


def reset_correlation(token: Token[CorrelationContext]) -> None:
    """Restore the correlation context captured by ``token``."""
    _CORRELATION.reset(token)


@contextmanager
def correlation_context(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    replace: bool = False,
) -> Iterator[CorrelationContext]:
    """Bind correlation identifiers for the duration of a block.

    ``replace=True`` makes the block start from exactly the supplied values, which
    clears any inherited identifier; the default overrides only what is supplied.
    """
    if replace:
        token = _CORRELATION.set(_EMPTY_CORRELATION)
        try:
            inner = bind_correlation(run_id=run_id, request_id=request_id, trace_id=trace_id)
            _CORRELATION.reset(inner)
            yield _CORRELATION.get()
        finally:
            _CORRELATION.reset(token)
        return
    token = bind_correlation(run_id=run_id, request_id=request_id, trace_id=trace_id)
    try:
        yield _CORRELATION.get()
    finally:
        reset_correlation(token)


def _active_trace_id() -> str | None:
    if not OTEL_AVAILABLE:
        return None
    span = _otel_trace.get_current_span()
    if span is None:
        return None
    span_context = span.get_span_context()
    if span_context is None or not span_context.is_valid:
        return None
    return f"{span_context.trace_id:032x}"


def get_correlation() -> CorrelationContext:
    """Return the effective correlation context.

    ``request_id`` falls back to the logging context used by the request
    middleware, and ``trace_id`` to the active OpenTelemetry span, so a caller
    never has to pass identifiers it already bound.
    """
    bound = _CORRELATION.get()
    request_id = bound.request_id if bound.request_id is not None else _logging_request_id()
    trace_id = bound.trace_id if bound.trace_id is not None else _active_trace_id()
    if request_id == bound.request_id and trace_id == bound.trace_id:
        return bound
    return CorrelationContext(run_id=bound.run_id, request_id=request_id, trace_id=trace_id)


def current_run_id() -> str | None:
    """Return the bound run id, or None."""
    return get_correlation().run_id


def current_request_id() -> str | None:
    """Return the bound request id (falling back to the logging context)."""
    return get_correlation().request_id


def current_trace_id() -> str | None:
    """Return the bound trace id (falling back to the active span)."""
    return get_correlation().trace_id


# --- attribute and label policy -------------------------------------------


def _normalize_label_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(".", "_").replace(" ", "_")


def _metric_label_key_violation(key: Any) -> str | None:
    if not isinstance(key, str) or not key.strip():
        return "label_key_not_string"
    normalized = _normalize_label_key(key)
    if credential_kind(normalized) is not None:
        return "label_key_looks_like_credential"
    if normalized in IDENTITY_LABEL_KEYS or normalized.endswith(IDENTITY_KEY_SUFFIXES):
        return "label_key_names_an_identifier"
    if not ACTION_PATTERN.match(normalized):
        return "label_key_malformed"
    return None


def _metric_label_value_violation(value: str) -> str | None:
    if len(value) > MAX_METRIC_LABEL_VALUE_LENGTH:
        return "label_value_unbounded"
    if CONTROL_CHARACTER_PATTERN.search(value):
        return "label_value_control_characters"
    if contains_identifier(value) or TRACE_ID_PATTERN.match(value):
        return "label_value_is_an_identifier"
    if credential_kind(value) is not None:
        return "label_value_looks_like_credential"
    if host_path_kind(value) is not None:
        return "label_value_is_a_host_path"
    return None


def _label_scalar(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, "g")
    if isinstance(value, str):
        return value
    return None


def _reject(scope: str, violation: str, key: str) -> TelemetryPolicyError:
    return TelemetryPolicyError(
        scope,
        violation,
        f"metric label {key!r} rejected by the telemetry label policy ({violation})",
    )


def validate_metric_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    """Validate and normalize one metric label set.

    Rejects identifier-named keys (``tenant_id``, ``user_id``, ``actor_ref``,
    ``*_id``/``*_ref``/``*_key``/...) and identifier-shaped, personal or
    unbounded values, which keeps metric labels bounded and free of personal
    data. Raises :class:`TelemetryPolicyError` on the first violation.
    """
    if not isinstance(labels, Mapping):
        raise TelemetryPolicyError(
            "metric_label_set", "labels_not_mapping", "metric labels must be a mapping"
        )
    validated: dict[str, str] = {}
    for key, value in labels.items():
        key_violation = _metric_label_key_violation(key)
        if key_violation is not None:
            raise _reject("metric_label_key", key_violation, str(key))
        normalized = _normalize_label_key(str(key))
        scalar = _label_scalar(value)
        if scalar is None:
            raise _reject("metric_label_value", "label_value_not_scalar", normalized)
        value_violation = _metric_label_value_violation(scalar)
        if value_violation is not None:
            raise _reject("metric_label_value", value_violation, normalized)
        validated[normalized] = scalar
    return validated


def validate_span_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Validate span/log attributes, which may carry tenant and run identifiers.

    Unlike a metric label, a trace attribute is retained with the trace and is not
    used for aggregation, so ``policyflow.tenant_id`` and ``policyflow.run_id`` are
    allowed. Credentials, host paths and oversized values are still rejected.
    """
    if not isinstance(attributes, Mapping):
        raise TelemetryPolicyError(
            "span_attribute_set", "attributes_not_mapping", "span attributes must be a mapping"
        )
    validated: dict[str, Any] = {}
    for key, value in attributes.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > SPAN_ATTRIBUTE_MAX_KEY_LENGTH
            or not ACTION_PATTERN.match(key)
        ):
            raise TelemetryPolicyError(
                "span_attribute",
                "attribute_key_malformed",
                f"span attribute key {str(key)!r} is not a dotted lower-case token",
            )
        if isinstance(value, str):
            if credential_kind(value) is not None:
                raise TelemetryPolicyError(
                    "span_attribute", "attribute_value_looks_like_credential", f"{key} rejected"
                )
            if host_path_kind(value) is not None:
                raise TelemetryPolicyError(
                    "span_attribute", "attribute_value_is_a_host_path", f"{key} rejected"
                )
            if len(value) > SPAN_ATTRIBUTE_MAX_VALUE_LENGTH:
                raise TelemetryPolicyError(
                    "span_attribute", "attribute_value_unbounded", f"{key} rejected"
                )
            validated[key] = value
            continue
        if isinstance(value, (bool, int, float)):
            validated[key] = value
            continue
        raise TelemetryPolicyError(
            "span_attribute", "attribute_value_not_scalar", f"{key} rejected"
        )
    return validated


def correlation_attributes(
    *,
    tenant_id: str | None = None,
    actor_ref: str | None = None,
) -> dict[str, Any]:
    """Return span/log attributes that correlate an operation with its run.

    Tenant and actor references are included here because trace and log retention
    controls cover them; they must never be passed to a metric helper.
    """
    correlation = get_correlation()
    attributes: dict[str, Any] = {}
    if correlation.run_id is not None:
        attributes[ATTR_RUN_ID] = correlation.run_id
    if correlation.request_id is not None:
        attributes[ATTR_REQUEST_ID] = correlation.request_id
    if correlation.trace_id is not None:
        attributes[ATTR_TRACE_ID] = correlation.trace_id
    if tenant_id is not None:
        attributes[ATTR_TENANT_ID] = tenant_id
    if actor_ref is not None:
        attributes[ATTR_ACTOR_REF] = actor_ref
    return validate_span_attributes(attributes)


def annotate_current_span(attributes: Mapping[str, Any]) -> None:
    """Set validated attributes on the active span when one is recording."""
    validated = validate_span_attributes(attributes)
    if not OTEL_AVAILABLE:
        return
    span = _otel_trace.get_current_span()
    if span is None or not span.is_recording():
        return
    for key, value in validated.items():
        span.set_attribute(key, value)


@contextmanager
def span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
    actor_ref: str | None = None,
) -> Iterator[Any]:
    """Start a span carrying the current correlation attributes."""
    combined = correlation_attributes(tenant_id=tenant_id, actor_ref=actor_ref)
    combined.update(validate_span_attributes(attributes or {}))
    tracer = _resolve_tracer()
    with tracer.start_as_current_span(name, attributes=combined) as active_span:
        yield active_span


# --- metric instruments ----------------------------------------------------


class _NullInstrument:
    """No-op instrument used when the OpenTelemetry API is unavailable."""

    def add(self, amount: Any, attributes: Mapping[str, Any] | None = None) -> None:
        return None

    def record(self, amount: Any, attributes: Mapping[str, Any] | None = None) -> None:
        return None


class _NullMeter:
    """No-op meter used when the OpenTelemetry API is unavailable."""

    def create_counter(self, name: str, **kwargs: Any) -> _NullInstrument:
        return _NullInstrument()

    def create_histogram(self, name: str, **kwargs: Any) -> _NullInstrument:
        return _NullInstrument()

    def create_up_down_counter(self, name: str, **kwargs: Any) -> _NullInstrument:
        return _NullInstrument()


@dataclass(frozen=True)
class _Instruments:
    api_requests: Any
    api_duration: Any
    db_pool_connections: Any
    db_pool_size: Any
    errors: Any
    authorization_decisions: Any


def _resolve_meter() -> Any:
    if _STATE.meter is not None:
        return _STATE.meter
    if not OTEL_AVAILABLE:
        return _NullMeter()
    return _otel_metrics.get_meter(_STATE.service_name)


class _NullSpan:
    """No-op span used when the OpenTelemetry API is unavailable."""

    def set_attribute(self, key: str, value: Any) -> None:
        return None


class _NullSpanContextManager:
    """No-op context manager returned by :class:`_NullTracer`."""

    def __enter__(self) -> _NullSpan:
        return _NullSpan()

    def __exit__(self, *exc_info: Any) -> None:
        return None


class _NullTracer:
    """No-op tracer used when the OpenTelemetry API is unavailable."""

    def start_as_current_span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> _NullSpanContextManager:
        return _NullSpanContextManager()


def _resolve_tracer() -> Any:
    if _STATE.tracer is not None:
        return _STATE.tracer
    if not OTEL_AVAILABLE:
        return _NullTracer()
    return _otel_trace.get_tracer(_STATE.service_name)


def _instruments() -> _Instruments:
    """Return the process-wide instrument set, created once under the state lock."""
    global _INSTRUMENTS
    with _STATE_LOCK:
        if _INSTRUMENTS is None:
            meter = _resolve_meter()
            _INSTRUMENTS = _Instruments(
                api_requests=meter.create_counter(
                    METRIC_API_REQUESTS,
                    unit="{request}",
                    description="API requests by method, templated route and status class",
                ),
                api_duration=meter.create_histogram(
                    METRIC_API_DURATION,
                    unit="s",
                    description="API request duration by method and templated route",
                ),
                db_pool_connections=meter.create_up_down_counter(
                    METRIC_DB_POOL_CONNECTIONS,
                    unit="{connection}",
                    description="Database pool connections by pool and state",
                ),
                db_pool_size=meter.create_up_down_counter(
                    METRIC_DB_POOL_SIZE,
                    unit="{connection}",
                    description="Configured database pool size by pool",
                ),
                errors=meter.create_counter(
                    METRIC_ERRORS,
                    unit="{error}",
                    description="Boundary errors by stable code, retryability and boundary",
                ),
                authorization_decisions=meter.create_counter(
                    METRIC_AUTHORIZATION_DECISIONS,
                    unit="{decision}",
                    description="Authorization decisions by action and decision",
                ),
            )
        return _INSTRUMENTS


# --- recording helpers -----------------------------------------------------


def _require_non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TelemetryPolicyError(
            "measurement", "not_a_non_negative_int", f"{name} must be a non-negative integer"
        )
    return value


def _status_class(status_code: Any) -> str:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise TelemetryPolicyError(
            "measurement", "status_code_not_int", "status_code must be an integer"
        )
    if not 100 <= status_code <= 599:
        raise TelemetryPolicyError(
            "measurement", "status_code_out_of_range", "status_code must be between 100 and 599"
        )
    return f"{status_code // 100}xx"


def record_api_request(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record one API request and its duration.

    ``route`` must be the templated route (``/api/v1/kb/{kb_id}``), never the raw
    path: a raw path carries identifiers and would be rejected as an
    identifier-shaped label value.
    """
    if not isinstance(method, str) or method.upper() not in HTTP_METHODS:
        raise TelemetryPolicyError(
            "metric_label_value", "http_method_unknown", "method must be a known HTTP method"
        )
    if isinstance(duration_seconds, bool) or not isinstance(
        duration_seconds, (int, float)
    ) or duration_seconds < 0:
        raise TelemetryPolicyError(
            "measurement", "duration_invalid", "duration_seconds must be a non-negative number"
        )
    status_class = _status_class(status_code)
    labels = validate_metric_labels(
        {"method": method.upper(), "route": route, "status_class": status_class}
    )
    instruments = _instruments()
    instruments.api_requests.add(1, attributes=labels)
    instruments.api_duration.record(
        float(duration_seconds),
        attributes={"method": labels["method"], "route": labels["route"]},
    )
    annotate_current_span(
        {
            **correlation_attributes(),
            ATTR_HTTP_METHOD: labels["method"],
            ATTR_HTTP_ROUTE: labels["route"],
            ATTR_HTTP_STATUS: status_code,
        }
    )


def record_db_pool_usage(
    *,
    pool_name: str = "default",
    size: int,
    checked_in: int,
    checked_out: int,
    overflow: int,
) -> dict[str, int]:
    """Record one database pool snapshot and return the applied deltas.

    Pool counters are up-down counters, so the helper converts the absolute
    snapshot from the engine into deltas against the previous snapshot for that
    pool. The returned mapping is exactly what was applied to the instruments,
    which keeps the behaviour verifiable without a collector.
    """
    pool_label = validate_metric_labels({"pool": pool_name})["pool"]
    snapshot = {
        "size": _require_non_negative_int(size, "size"),
        "checked_in": _require_non_negative_int(checked_in, "checked_in"),
        "checked_out": _require_non_negative_int(checked_out, "checked_out"),
        "overflow": _require_non_negative_int(overflow, "overflow"),
    }
    with _STATE_LOCK:
        previous = _POOL_SNAPSHOTS.get(pool_label, {})
        deltas = {
            name: value - int(previous.get(name, 0)) for name, value in snapshot.items()
        }
        _POOL_SNAPSHOTS[pool_label] = dict(snapshot)
    instruments = _instruments()
    for state in DB_POOL_STATES:
        if deltas[state]:
            instruments.db_pool_connections.add(
                deltas[state], attributes={"pool": pool_label, "state": state}
            )
    if deltas["size"]:
        instruments.db_pool_size.add(deltas["size"], attributes={"pool": pool_label})
    annotate_current_span(
        {
            **correlation_attributes(),
            ATTR_DB_POOL: pool_label,
            ATTR_DB_POOL_SIZE: snapshot["size"],
            ATTR_DB_POOL_CHECKED_IN: snapshot["checked_in"],
            ATTR_DB_POOL_CHECKED_OUT: snapshot["checked_out"],
            ATTR_DB_POOL_OVERFLOW: snapshot["overflow"],
        }
    )
    return deltas


def record_error(
    code: str | ErrorCode,
    *,
    retryable: bool | None = None,
    boundary: str | None = None,
) -> None:
    """Record one boundary error by stable code.

    ``retryable`` defaults to the declared semantics of the code, so an error
    counter cannot disagree with the Error Contract it belongs to.
    """
    resolved_code = code.value if isinstance(code, ErrorCode) else str(code)
    if not REASON_CODE_PATTERN.match(resolved_code):
        raise TelemetryPolicyError(
            "metric_label_value",
            "error_code_malformed",
            "error code must be a stable uppercase code",
        )
    semantics = semantics_for(resolved_code)
    resolved_retryable = bool(retryable) if retryable is not None else bool(
        semantics is not None and semantics.retryable
    )
    labels = validate_metric_labels({"code": resolved_code, "retryable": resolved_retryable})
    if boundary is not None:
        labels.update(validate_metric_labels({"boundary": boundary}))
    _instruments().errors.add(1, attributes=labels)
    span_attributes: dict[str, Any] = {
        **correlation_attributes(),
        ATTR_ERROR_CODE: resolved_code,
        ATTR_ERROR_RETRYABLE: resolved_retryable,
    }
    if boundary is not None:
        span_attributes[ATTR_ERROR_BOUNDARY] = boundary
    annotate_current_span(span_attributes)


def record_authorization_decision(
    action: str,
    *,
    allowed: bool,
    reason_code: str | None = None,
) -> None:
    """Record one authorization decision, counted by action and allow/deny."""
    if not isinstance(action, str) or not ACTION_PATTERN.match(action):
        raise TelemetryPolicyError(
            "metric_label_value",
            "authorization_action_malformed",
            "action must be a lower-case dotted action token",
        )
    decision = "allow" if allowed else "deny"
    labels = validate_metric_labels(
        {"action": action, "decision": decision, "reason_code": reason_code or "none"}
    )
    _instruments().authorization_decisions.add(1, attributes=labels)
    annotate_current_span(
        {
            **correlation_attributes(),
            ATTR_AUTH_ACTION: labels["action"],
            ATTR_AUTH_DECISION: labels["decision"],
        }
    )


# --- configuration ---------------------------------------------------------


def otel_available() -> bool:
    """Return True when the OpenTelemetry API is importable."""
    return OTEL_AVAILABLE


def telemetry_configuration() -> TelemetryConfiguration:
    """Return the effective configuration without changing it."""
    with _STATE_LOCK:
        return _configuration_locked()


def _configuration_locked() -> TelemetryConfiguration:
    if _STATE.meter is None:
        meter_source = "global_proxy" if OTEL_AVAILABLE else "unavailable"
    elif _STATE.meter_provider is not None:
        meter_source = "sdk"
    else:
        meter_source = "injected"
    if _STATE.tracer is None:
        tracer_source = "global_proxy" if OTEL_AVAILABLE else "unavailable"
    elif _STATE.tracer_provider is not None:
        tracer_source = "sdk"
    else:
        tracer_source = "injected"
    return TelemetryConfiguration(
        service_name=_STATE.service_name,
        enabled=_STATE.enabled,
        otel_available=OTEL_AVAILABLE,
        exporter_configured=_STATE.exporter_endpoint is not None,
        meter_source=meter_source,
        tracer_source=tracer_source,
    )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def _global_provider_is_unset(provider: Any) -> bool:
    """Return True when the global provider is still the API proxy.

    The OpenTelemetry API exposes no public predicate for "no SDK configured", so
    the proxy classes are identified here; this keeps a host-configured provider
    from being overridden by this module.
    """
    if _ProxyTracerProvider is not None and isinstance(provider, _ProxyTracerProvider):
        return True
    return type(provider).__name__ == "_ProxyMeterProvider"


def configure_telemetry(
    *,
    enabled: bool | None = None,
    service_name: str = SERVICE_NAME,
    exporter_endpoint: str | None = None,
    meter: Any = None,
    tracer: Any = None,
) -> TelemetryConfiguration:
    """Configure telemetry explicitly; repeated identical calls are a no-op.

    ``enabled=None`` reads ``POLICYFLOW_TELEMETRY_ENABLED`` and defaults to off.
    No exporter is built unless ``exporter_endpoint`` or
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, so the default configuration performs
    no network calls and starts no threads. ``meter``/``tracer`` inject an
    externally owned meter or tracer (used by tests and by hosts that already
    configured OpenTelemetry themselves).
    """
    resolved_enabled = _env_flag(TELEMETRY_ENABLED_ENV) if enabled is None else bool(enabled)
    resolved_endpoint = exporter_endpoint or os.environ.get(OTLP_ENDPOINT_ENV) or None
    signature = (
        resolved_enabled,
        service_name,
        resolved_endpoint,
        id(meter),
        id(tracer),
    )
    with _STATE_LOCK:
        global _INSTRUMENTS
        if _STATE.configuration_signature == signature:
            return _configuration_locked()
        resolved_meter = meter
        resolved_tracer = tracer
        meter_provider = None
        tracer_provider = None
        if resolved_meter is None and resolved_enabled and OTEL_AVAILABLE:
            meter_provider = _build_meter_provider(service_name, resolved_endpoint)
            resolved_meter = meter_provider.get_meter(service_name)
        if resolved_tracer is None and resolved_enabled and OTEL_AVAILABLE:
            tracer_provider = _build_tracer_provider(service_name, resolved_endpoint)
            resolved_tracer = tracer_provider.get_tracer(service_name)
        _STATE.service_name = service_name
        _STATE.enabled = resolved_enabled
        _STATE.exporter_endpoint = resolved_endpoint
        _STATE.meter = resolved_meter
        _STATE.tracer = resolved_tracer
        _STATE.meter_provider = meter_provider
        _STATE.tracer_provider = tracer_provider
        _STATE.configuration_signature = signature
        _INSTRUMENTS = None
        _install_global_providers(meter_provider, tracer_provider)
        return _configuration_locked()


def _install_global_providers(meter_provider: Any, tracer_provider: Any) -> None:
    if not OTEL_AVAILABLE:
        return
    if meter_provider is not None and _global_provider_is_unset(_otel_metrics.get_meter_provider()):
        _otel_metrics.set_meter_provider(meter_provider)
    if tracer_provider is not None and _global_provider_is_unset(
        _otel_trace.get_tracer_provider()
    ):
        _otel_trace.set_tracer_provider(tracer_provider)


def _build_meter_provider(service_name: str, endpoint: str | None) -> Any:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service_name})
    if not endpoint:
        return MeterProvider(resource=resource)
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint.rstrip('/')}/v1/metrics")
    )
    return MeterProvider(resource=resource, metric_readers=[reader])


def _build_tracer_provider(service_name: str, endpoint: str | None) -> Any:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if not endpoint:
        return provider
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    return provider


def reset_telemetry() -> None:
    """Drop configuration, instruments and pool snapshots.

    Test seam. Globally installed OpenTelemetry providers cannot be unset by this
    module, so a reset returns this module to lazy proxy instruments without
    touching a provider the host still holds.
    """
    global _INSTRUMENTS
    with _STATE_LOCK:
        _STATE.service_name = SERVICE_NAME
        _STATE.enabled = False
        _STATE.exporter_endpoint = None
        _STATE.meter = None
        _STATE.tracer = None
        _STATE.meter_provider = None
        _STATE.tracer_provider = None
        _STATE.configuration_signature = None
        _INSTRUMENTS = None
        _POOL_SNAPSHOTS.clear()
    _CORRELATION.set(_EMPTY_CORRELATION)
