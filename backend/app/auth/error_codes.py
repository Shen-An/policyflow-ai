"""Shared stable error codes for identity and authorization boundaries.

The values are the exact vocabulary from ``contracts/internal-contracts.md``
("Error Contract"). They live in their own module so that both the identity
layer and the authorization layer can depend on them without importing each
other.
"""

from __future__ import annotations

__all__ = ["AUTH_FORBIDDEN", "RESOURCE_NOT_FOUND", "TENANT_NOT_FOUND"]

AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
TENANT_NOT_FOUND = "TENANT_NOT_FOUND"
RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
