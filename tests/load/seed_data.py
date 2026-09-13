"""Reproducible multi-tenant fixtures for capacity and isolation profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

SEED_NAMESPACE = uuid.UUID("6f3d4a5d-7b4d-4d53-9a2b-2a7b69b8d4b3")
ROLE_CODES = ("employee", "approver", "admin")


def stable_id(seed: int, kind: str, key: str) -> str:
    """Create a deterministic opaque ID for a fixture entity."""

    return str(uuid.uuid5(SEED_NAMESPACE, f"{seed}:{kind}:{key}"))


def _quota(seed: int, tenant_index: int) -> dict[str, int]:
    base = 300 + tenant_index * 100
    return {
        "requests_per_minute": base,
        "max_concurrency": 20 + tenant_index * 5,
        "llm_tokens_per_minute": 200_000 + tenant_index * 50_000,
        "llm_budget_usd_cents_per_day": 2_000 + tenant_index * 500,
        "queue_max_items": 1_000 + tenant_index * 250,
    }


def build_seed_data(*, seed: int = 20260913, tenant_count: int = 2) -> dict[str, Any]:
    """Build two or more isolated tenants with stable roles and versions."""

    if tenant_count < 2:
        raise ValueError("tenant_count must be at least 2 for isolation profiles")

    tenants: list[dict[str, Any]] = []
    users: list[dict[str, Any]] = []
    policies: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    for tenant_index in range(tenant_count):
        tenant_number = tenant_index + 1
        tenant_id = stable_id(seed, "tenant", str(tenant_number))
        tenant_code = f"tenant-{tenant_number:02d}"
        tenant = {
            "id": tenant_id,
            "code": tenant_code,
            "name": f"PolicyFlow Test Tenant {tenant_number}",
            "status": "active",
            "quota": _quota(seed, tenant_index),
        }
        tenants.append(tenant)

        for role_code in ROLE_CODES:
            user_key = f"{tenant_code}:{role_code}"
            user = {
                "id": stable_id(seed, "user", user_key),
                "tenant_id": tenant_id,
                "username": f"{tenant_code}-{role_code}",
                "external_subject": f"{seed}:{user_key}",
                "role_codes": [role_code],
                "status": "active",
            }
            users.append(user)

        policy_key = f"{tenant_code}:travel-policy"
        policy_id = stable_id(seed, "policy", policy_key)
        policy_version_id = stable_id(seed, "policy-version", f"{policy_key}:v1")
        policies.append(
            {
                "id": policy_id,
                "tenant_id": tenant_id,
                "code": "travel-policy",
                "title": "Business Travel and Reimbursement Policy",
                "version_id": policy_version_id,
                "version": "v1",
                "content": (
                    "Employees must submit receipts within 30 days. "
                    f"Tenant-specific approval lane: {tenant_code}."
                ),
                "active": True,
            }
        )

        material_key = f"{tenant_code}:travel-policy.pdf:v1"
        material_sha256 = hashlib.sha256(
            f"{seed}:{material_key}:fixture-bytes".encode()
        ).hexdigest()
        materials.append(
            {
                "id": stable_id(seed, "material", material_key),
                "tenant_id": tenant_id,
                "filename": "travel-policy.pdf",
                "media_type": "application/pdf",
                "version_id": stable_id(seed, "material-version", material_key),
                "version": "v1",
                "sha256": material_sha256,
                "status": "available",
                "retrievable": True,
            }
        )

    return {
        "schema_version": "1",
        "seed": seed,
        "tenant_count": tenant_count,
        "role_codes": list(ROLE_CODES),
        "tenants": tenants,
        "users": users,
        "policies": policies,
        "materials": materials,
    }


def write_seed_data(path: str | Path, *, seed: int = 20260913, tenant_count: int = 2) -> Path:
    """Write a stable JSON fixture and return its path."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_seed_data(seed=seed, tenant_count=tenant_count), ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return output


generate_seed_data = build_seed_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--tenant-count", type=int, default=2)
    args = parser.parse_args()
    write_seed_data(args.output, seed=args.seed, tenant_count=args.tenant_count)


if __name__ == "__main__":
    main()
