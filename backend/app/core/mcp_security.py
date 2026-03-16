"""Encryption helpers for MCP commands and sensitive configuration values."""

import base64
import hashlib
import json
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.exceptions import ApplicationError
from backend.app.core.redaction import is_sensitive_key, redact_sensitive

ENCRYPTED_PREFIX = "enc:v1:"
ENCRYPTED_VALUE_KEY = "__policyflow_encrypted__"


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str | None, secret_key: str) -> str:
    if not value:
        return ""
    if value.startswith(ENCRYPTED_PREFIX):
        return value
    token = _fernet(secret_key).encrypt(value.encode("utf-8")).decode("ascii")
    return ENCRYPTED_PREFIX + token


def decrypt_secret(value: str, secret_key: str) -> str:
    if not value:
        return ""
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    try:
        return (
            _fernet(secret_key)
            .decrypt(value.removeprefix(ENCRYPTED_PREFIX).encode("ascii"))
            .decode("utf-8")
        )
    except InvalidToken as exc:
        raise ApplicationError(
            "SECRET_DECRYPT_FAILED",
            "Stored secret could not be decrypted",
            500,
        ) from exc


def protect_command(command: str | None, secret_key: str) -> str:
    return encrypt_secret(command, secret_key)


def reveal_command(command: str, secret_key: str) -> str:
    try:
        return decrypt_secret(command, secret_key)
    except ApplicationError as exc:
        raise ApplicationError(
            "MCP_CONFIG_DECRYPT_FAILED",
            "MCP configuration could not be decrypted",
            500,
        ) from exc


def _encrypt_value(value: Any, secret_key: str) -> dict[str, str]:
    serialized = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    token = _fernet(secret_key).encrypt(serialized).decode("ascii")
    return {ENCRYPTED_VALUE_KEY: token}


def protect_config(value: Any, secret_key: str) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                item
                if is_sensitive_key(str(key))
                and isinstance(item, dict)
                and set(item) == {ENCRYPTED_VALUE_KEY}
                else _encrypt_value(item, secret_key)
                if is_sensitive_key(str(key))
                else protect_config(item, secret_key)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [protect_config(item, secret_key) for item in value]
    return value


def reveal_config(value: Any, secret_key: str) -> Any:
    if isinstance(value, dict):
        if set(value) == {ENCRYPTED_VALUE_KEY}:
            try:
                serialized = _fernet(secret_key).decrypt(
                    str(value[ENCRYPTED_VALUE_KEY]).encode("ascii")
                )
                return json.loads(serialized)
            except (InvalidToken, json.JSONDecodeError) as exc:
                raise ApplicationError(
                    "MCP_CONFIG_DECRYPT_FAILED",
                    "MCP configuration could not be decrypted",
                    500,
                ) from exc
        return {str(key): reveal_config(item, secret_key) for key, item in value.items()}
    if isinstance(value, list):
        return [reveal_config(item, secret_key) for item in value]
    return value


def config_summary(value: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], redact_sensitive(value))
