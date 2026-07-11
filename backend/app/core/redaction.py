"""Recursive sensitive-value redaction for persisted and returned summaries."""

from typing import Any

SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "credential",
)
REDACTED_VALUE = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                REDACTED_VALUE
                if is_sensitive_key(str(key))
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value
