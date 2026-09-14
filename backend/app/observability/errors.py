"""Stable error vocabulary for API and worker boundaries.

This module carries the ``Error Contract`` of
``specs/001-enterprise-agent-refactor/contracts/internal-contracts.md``: every
boundary failure travels as one of the declared codes, states whether a retry is
safe, and may carry a server-controlled retry delay. A boundary error never
reveals another tenant's existence, storage keys, host paths, credentials or raw
provider payloads.

It lives in the observability package because the audit boundary (``audit.py``)
and the telemetry helpers (``telemetry.py``) both validate against the same
vocabulary, and it builds on :class:`backend.app.core.exceptions.ApplicationError`
so the existing FastAPI exception handlers keep formatting boundary errors.

Stability rules enforced in code:

* a code is a member of :class:`ErrorCode` and equals its uppercase string value;
* the transport status and retry semantics of a code are declared once in
  :data:`ERROR_SEMANTICS`, so the same code cannot answer differently at two
  boundaries;
* a retry delay is only accepted for a retryable code, must be positive and is
  capped, because it is a server decision rather than a client hint;
* a message or detail set that leaks forbidden content is rejected outright.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from backend.app.core.exceptions import ApplicationError
from backend.app.observability.guards import leak_kind

MAX_RETRY_AFTER_SECONDS = 3600.0


class ErrorCode(StrEnum):
    """Stable boundary error codes; values are frozen uppercase strings."""

    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    TENANT_NOT_FOUND = "TENANT_NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_STALE = "APPROVAL_STALE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    CAPACITY_SATURATED = "CAPACITY_SATURATED"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SANDBOX_POLICY_DENIED = "SANDBOX_POLICY_DENIED"
    SANDBOX_LIMIT_EXCEEDED = "SANDBOX_LIMIT_EXCEEDED"
    CONNECTOR_UNKNOWN_OUTCOME = "CONNECTOR_UNKNOWN_OUTCOME"
    RECOVERABLE_FAILURE = "RECOVERABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    AUDIT_CONTENT_REJECTED = "AUDIT_CONTENT_REJECTED"


@dataclass(frozen=True)
class ErrorSemantics:
    """Transport status and retry semantics declared for one stable code."""

    code: ErrorCode
    status_code: int
    retryable: bool
    retry_after_seconds: float | None
    message: str


ERROR_SEMANTICS: dict[ErrorCode, ErrorSemantics] = {
    ErrorCode.AUTH_FORBIDDEN: ErrorSemantics(
        ErrorCode.AUTH_FORBIDDEN, 403, False, None, "Permission denied"
    ),
    ErrorCode.TENANT_NOT_FOUND: ErrorSemantics(
        ErrorCode.TENANT_NOT_FOUND, 404, False, None, "Resource not found"
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorSemantics(
        ErrorCode.RESOURCE_NOT_FOUND, 404, False, None, "Resource not found"
    ),
    ErrorCode.VERSION_CONFLICT: ErrorSemantics(
        ErrorCode.VERSION_CONFLICT, 409, False, None, "Resource version conflict"
    ),
    ErrorCode.IDEMPOTENCY_CONFLICT: ErrorSemantics(
        ErrorCode.IDEMPOTENCY_CONFLICT,
        409,
        False,
        None,
        "Idempotency key reused with a different request",
    ),
    ErrorCode.APPROVAL_REQUIRED: ErrorSemantics(
        ErrorCode.APPROVAL_REQUIRED, 409, False, None, "Approval required before execution"
    ),
    ErrorCode.APPROVAL_STALE: ErrorSemantics(
        ErrorCode.APPROVAL_STALE, 409, False, None, "Approval is stale"
    ),
    ErrorCode.QUOTA_EXCEEDED: ErrorSemantics(
        ErrorCode.QUOTA_EXCEEDED, 429, True, 60.0, "Quota exceeded"
    ),
    ErrorCode.CAPACITY_SATURATED: ErrorSemantics(
        ErrorCode.CAPACITY_SATURATED, 503, True, 5.0, "Capacity saturated"
    ),
    ErrorCode.RETRIEVAL_UNAVAILABLE: ErrorSemantics(
        ErrorCode.RETRIEVAL_UNAVAILABLE, 503, True, 2.0, "Retrieval is unavailable"
    ),
    ErrorCode.INSUFFICIENT_EVIDENCE: ErrorSemantics(
        ErrorCode.INSUFFICIENT_EVIDENCE, 422, False, None, "Insufficient evidence to answer"
    ),
    ErrorCode.SANDBOX_POLICY_DENIED: ErrorSemantics(
        ErrorCode.SANDBOX_POLICY_DENIED, 403, False, None, "Sandbox execution policy denied"
    ),
    ErrorCode.SANDBOX_LIMIT_EXCEEDED: ErrorSemantics(
        ErrorCode.SANDBOX_LIMIT_EXCEEDED, 429, True, 30.0, "Sandbox resource limit exceeded"
    ),
    ErrorCode.CONNECTOR_UNKNOWN_OUTCOME: ErrorSemantics(
        ErrorCode.CONNECTOR_UNKNOWN_OUTCOME,
        502,
        False,
        None,
        "Connector outcome unknown; reconcile before retry",
    ),
    ErrorCode.RECOVERABLE_FAILURE: ErrorSemantics(
        ErrorCode.RECOVERABLE_FAILURE, 503, True, 5.0, "Temporary failure; the operation can be retried"
    ),
    ErrorCode.TERMINAL_FAILURE: ErrorSemantics(
        ErrorCode.TERMINAL_FAILURE, 500, False, None, "Terminal failure"
    ),
    ErrorCode.AUDIT_UNAVAILABLE: ErrorSemantics(
        ErrorCode.AUDIT_UNAVAILABLE, 503, True, 5.0, "Audit sink unavailable"
    ),
    ErrorCode.AUDIT_CONTENT_REJECTED: ErrorSemantics(
        ErrorCode.AUDIT_CONTENT_REJECTED, 400, False, None, "Audit event content rejected"
    ),
}


def semantics_for(code: str | ErrorCode) -> ErrorSemantics | None:
    """Return the declared semantics of ``code``, or None when it is undeclared."""
    try:
        resolved = ErrorCode(code)
    except ValueError:
        return None
    return ERROR_SEMANTICS[resolved]


def _assert_leak_free(*values: Any) -> None:
    serialized = json.dumps(values, ensure_ascii=False, default=str)
    kind = leak_kind(serialized)
    if kind is not None:
        raise ValueError(
            f"Boundary error content violates the Error Contract ({kind}); "
            "remove credentials, host paths and provider payloads"
        )


class ContractError(ApplicationError):
    """A boundary failure carrying stable code, retry flag and optional delay.

    Subclasses :class:`ApplicationError` so the registered FastAPI handlers keep
    formatting it; the response body stays ``code``/``message``/``details`` plus
    the retry fields produced by :meth:`public_payload`.
    """

    def __init__(
        self,
        code: str | ErrorCode,
        *,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
        status_code: int | None = None,
    ) -> None:
        semantics = semantics_for(code)
        if semantics is None:
            raise ValueError(f"Undeclared boundary error code: {code!r}")
        resolved_code = semantics.code
        resolved_retryable = semantics.retryable if retryable is None else bool(retryable)
        resolved_status = semantics.status_code if status_code is None else int(status_code)
        resolved_message = semantics.message if message is None else message

        if retry_after_seconds is not None:
            if not resolved_retryable:
                raise ValueError(
                    f"{resolved_code} is not retryable and cannot carry a retry delay"
                )
            if not 0 < float(retry_after_seconds) <= MAX_RETRY_AFTER_SECONDS:
                raise ValueError(
                    "retry_after_seconds must be positive and at most "
                    f"{MAX_RETRY_AFTER_SECONDS:g} seconds"
                )
            resolved_delay: float | None = float(retry_after_seconds)
        elif semantics.retry_after_seconds is not None and resolved_retryable:
            resolved_delay = semantics.retry_after_seconds
        else:
            resolved_delay = None

        resolved_details: dict[str, Any] = dict(details) if details else {}
        _assert_leak_free(resolved_message, resolved_details)

        super().__init__(str(resolved_code), resolved_message, resolved_status, resolved_details)
        self.code = str(resolved_code)
        self.semantics = semantics
        self.retryable = resolved_retryable
        self.retry_after_seconds = resolved_delay

    def public_payload(self) -> dict[str, Any]:
        """Return the client-safe error payload for this failure."""
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details) if self.details else {},
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
        }

    def retry_after_header(self) -> dict[str, str]:
        """Return the server-controlled ``Retry-After`` header, when applicable."""
        if self.retry_after_seconds is None:
            return {}
        return {"Retry-After": str(math.ceil(self.retry_after_seconds))}


class ResourceVisibility(StrEnum):
    """Outcome of a tenant-scoped resource lookup.

    ``ABSENT`` and ``FOREIGN_TENANT`` are distinct facts inside the service and
    must stay indistinguishable outside it, which is why callers never pass the
    visibility to an error factory other than
    :func:`resolve_resource_visibility`.
    """

    VISIBLE = "visible"
    ABSENT = "absent"
    FOREIGN_TENANT = "foreign_tenant"


def resource_not_found() -> ContractError:
    """Return the single non-enumerating not-found error.

    The message, details and status are constant by construction: the error
    carries no resource reference, tenant hint, storage key or path.
    """
    return ContractError(ErrorCode.RESOURCE_NOT_FOUND)


def resolve_resource_visibility(visibility: ResourceVisibility | str) -> ContractError | None:
    """Return the not-found error for every non-visible lookup result.

    Returning one error object for ``ABSENT`` and ``FOREIGN_TENANT`` is what makes
    a missing resource and another tenant's resource indistinguishable to a
    caller, so an attacker cannot probe for existing identifiers.
    """
    resolved = ResourceVisibility(visibility)
    if resolved is ResourceVisibility.VISIBLE:
        return None
    return resource_not_found()
