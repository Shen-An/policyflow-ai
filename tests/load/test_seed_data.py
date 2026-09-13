"""Contract tests for reproducible capacity fixtures."""

from __future__ import annotations

import json

from tests.load.seed_data import build_seed_data, write_seed_data


def test_seed_data_is_stable_and_isolated(tmp_path) -> None:
    first = build_seed_data(seed=42)
    second = build_seed_data(seed=42)
    assert first == second
    assert first["tenant_count"] == 2
    assert len(first["users"]) == 6
    assert {user["role_codes"][0] for user in first["users"]} == {
        "employee",
        "approver",
        "admin",
    }
    assert len({item["tenant_id"] for item in first["materials"]}) == 2
    assert {item["filename"] for item in first["materials"]} == {"travel-policy.pdf"}
    assert first["tenants"][0]["quota"] != first["tenants"][1]["quota"]

    output = write_seed_data(tmp_path / "seed.json", seed=42)
    assert json.loads(output.read_text(encoding="utf-8")) == first
