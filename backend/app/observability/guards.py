"""Dependency-free content guards shared by the audit and telemetry boundaries.

The guards implement the prohibition list of the ``AuditSink`` contract:
credentials, host file paths, raw provider payloads and unrestricted file
bodies must never be persisted or exported.

Design rules:

* Every guard returns the *kind* of violation, never the offending value, so a
  rejection message can be logged or returned without re-leaking the secret,
  path or payload that triggered it.
* Guards are pure functions over ``str``/``Mapping`` values. They perform no
  I/O, create no threads and never raise for ordinary text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# --- size and shape limits -------------------------------------------------

MAX_METADATA_STRING_LENGTH = 2048
MAX_METADATA_BYTES = 16384
MAX_METADATA_DEPTH = 6
MAX_METADATA_ENTRIES = 256
MAX_REFERENCE_LENGTH = 512
MAX_REASON_CODE_LENGTH = 64
MAX_METRIC_LABEL_VALUE_LENGTH = 64

# --- identifier shapes -----------------------------------------------------

UUID_PATTERN = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
UUID_SUBSTRING_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
EMAIL_PATTERN = re.compile(r"\A[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}\Z")
EMAIL_SUBSTRING_PATTERN = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}")
TRACE_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
METADATA_KEY_PATTERN = re.compile(r"\A[a-z][a-z0-9_]{0,62}\Z")
REASON_CODE_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]{1,63}\Z")
ACTION_PATTERN = re.compile(r"\A[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")

# --- host file paths -------------------------------------------------------

HOST_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "windows_drive",
        re.compile(r"\b[A-Za-z]:[\\/](?:[^\s\"'|<>]*[^\s\\/\"'|<>])?"),
    ),
    ("unc_share", re.compile(r"\\\\[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._$-]+)+")),
    ("file_url", re.compile(r"(?i)\bfile://[^\s\"'<>]*")),
    (
        "posix_system",
        re.compile(
            r"(?<![\w:/])/(?:etc|home|root|var|usr|opt|srv|mnt|media|proc|sys|tmp|Users)"
            r"(?:/[A-Za-z0-9._@+-]+)+/?"
        ),
    ),
)

PATH_MARKER = "[REDACTED_PATH]"

# --- credentials -----------------------------------------------------------

CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    (
        "basic_auth",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://[^/\s:@]{1,64}:[^/\s@]{1,128}@"),
    ),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret"
            r"|password|passwd|pwd)\b\s*[:=]\s*\S{6,}"
        ),
    ),
    ("provider_token", re.compile(r"\b(?:sk|rk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9._-]{12,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# --- raw provider payloads -------------------------------------------------

# A single one of these keys already identifies a raw provider response body.
STRONG_PROVIDER_PAYLOAD_KEYS = frozenset(
    {
        "choices",
        "embedding",
        "embeddings",
        "content_block",
        "content_block_delta",
        "finish_reason",
        "stop_reason",
        "logprobs",
        "system_fingerprint",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
)
# Only a combination of these indicates a provider response envelope.
WEAK_PROVIDER_PAYLOAD_KEYS = frozenset(
    {"model", "usage", "delta", "object", "created", "output", "data", "index"}
)
PROVIDER_PAYLOAD_MARKER = "[REDACTED_PROVIDER_PAYLOAD]"

_BASE64_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\r\n"
)


def is_uuid_like(text: str) -> bool:
    """Return True when ``text`` is a canonical 8-4-4-4-12 UUID string."""
    return bool(UUID_PATTERN.match(text))


def is_trace_id_like(text: str) -> bool:
    """Return True when ``text`` is a 32 character lowercase hex trace id."""
    return bool(TRACE_ID_PATTERN.match(text))


def is_email_like(text: str) -> bool:
    """Return True when ``text`` looks like a personal email address."""
    return bool(EMAIL_PATTERN.match(text))


def has_control_characters(text: str) -> bool:
    """Return True when ``text`` contains a control character (including newlines)."""
    return bool(CONTROL_CHARACTER_PATTERN.search(text))


def host_path_kind(text: str) -> str | None:
    """Return the kind of host file path found in ``text``, or None."""
    for kind, pattern in HOST_PATH_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def credential_kind(text: str) -> str | None:
    """Return the kind of credential found in ``text``, or None."""
    for kind, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def strip_credentials(text: str, replacement: str = "[REDACTED]") -> str:
    """Replace every credential occurrence in ``text`` with ``replacement``."""
    cleaned = text
    for _, pattern in CREDENTIAL_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def strip_host_paths(text: str, replacement: str = PATH_MARKER) -> str:
    """Replace every host file path occurrence in ``text`` with ``replacement``."""
    cleaned = text
    for _, pattern in HOST_PATH_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def looks_like_provider_payload(value: Mapping[str, Any]) -> bool:
    """Return True when ``value`` is a raw provider response body."""
    keys = {str(key).strip().casefold() for key in value}
    if keys & STRONG_PROVIDER_PAYLOAD_KEYS:
        return True
    return len(keys & WEAK_PROVIDER_PAYLOAD_KEYS) >= 3


def looks_like_binary_blob(text: str) -> bool:
    """Return True when ``text`` is a long base64 blob rather than a summary value.

    A real encoded body mixes casing and digits; a long run of one repeated
    character is ordinary text and must not be mistaken for a payload.
    """
    probe = text.strip()
    if len(probe) < 256 or len(set(probe)) < 8:
        return False
    return all(character in _BASE64_ALPHABET for character in probe)


def contains_identifier(text: str) -> bool:
    """Return True when ``text`` embeds a UUID or email address anywhere in it.

    Used for metric label values, where an identifier inside a templated route
    (``/api/v1/kb/3f2b...``) is exactly the unbounded cardinality the label policy
    exists to prevent.
    """
    return bool(
        UUID_SUBSTRING_PATTERN.search(text) or EMAIL_SUBSTRING_PATTERN.search(text)
    )


def leak_kind(text: str) -> str | None:
    """Return the kind of forbidden content in ``text``, or None.

    Used where forbidden content must be rejected rather than stripped, for
    example inside an error message that will be returned to a client.
    """
    credential = credential_kind(text)
    if credential is not None:
        return f"credential:{credential}"
    path = host_path_kind(text)
    if path is not None:
        return f"host_path:{path}"
    return None
