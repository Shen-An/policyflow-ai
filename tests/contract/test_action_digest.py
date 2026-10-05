"""T101 [US1] the action digest: canonical, total, and change-sensitive.

Pure unit tests (no infrastructure). The digest is the proof that "what was
approved" equals "what was executed", so these pin the three properties the rest
of the approval flow leans on:

* it covers exactly the inputs ``data-model.md`` says bind an approval -- no more,
  no less, checked at construction;
* it is order-independent, so a cosmetic reserialisation of the same action does
  not spuriously invalidate a valid approval;
* every bound input changes it, so no bound input can change under an approval
  without the digest noticing.
"""

from __future__ import annotations

import pytest

from backend.app.approvals.digest import (
    ActionDigestInput,
    PermissionSnapshot,
    compute_action_digest,
    digests_match,
)
from backend.app.db.models import APPROVAL_DIGEST_INPUTS


def _input(**overrides) -> ActionDigestInput:
    base = {
        "action": "reimbursement_submit",
        "destination": "expense-system",
        "source_versions": ["v1", "v2"],
        "output_versions": ["out-1"],
        "file_hashes": {"a.pdf": "a" * 64, "b.pdf": "b" * 64},
        "diff": "--- a\n+++ b",
        "evidence_set": ["ev-1", "ev-2"],
        "permission_snapshot": {"tenant_id": "t", "user_id": "u", "roles": ["approver"]},
        "side_effects": ["external_submission"],
    }
    base.update(overrides)
    return ActionDigestInput(**base)


def test_digest_covers_exactly_the_bound_inputs() -> None:
    canonical = _input().canonical()
    assert set(canonical) == set(APPROVAL_DIGEST_INPUTS), (
        "the digest must cover exactly the inputs the model declares binding"
    )


def test_digest_is_deterministic_and_order_independent() -> None:
    a = compute_action_digest(_input(source_versions=["v1", "v2"], evidence_set=["ev-1", "ev-2"]))
    b = compute_action_digest(_input(source_versions=["v2", "v1"], evidence_set=["ev-2", "ev-1"]))
    assert a == b, "reordering the same version/evidence sets must not change the digest"
    # Dict key order in the permission snapshot must not matter either.
    c = compute_action_digest(
        _input(permission_snapshot={"user_id": "u", "roles": ["approver"], "tenant_id": "t"})
    )
    assert a == c
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "something_else"},
        {"destination": "other"},
        {"source_versions": ["v1", "v3"]},
        {"output_versions": ["out-2"]},
        {"file_hashes": {"a.pdf": "a" * 64, "b.pdf": "c" * 64}},
        {"diff": "--- a\n+++ different"},
        {"evidence_set": ["ev-1"]},
        {"permission_snapshot": {"tenant_id": "t", "user_id": "u", "roles": ["admin"]}},
        {"side_effects": ["external_submission", "internal_write"]},
    ],
)
def test_every_bound_input_changes_the_digest(overrides: dict) -> None:
    baseline = compute_action_digest(_input())
    changed = compute_action_digest(_input(**overrides))
    assert changed != baseline, f"changing {list(overrides)} did not change the digest"


def test_digests_match_is_case_insensitive_and_constant_time() -> None:
    digest = compute_action_digest(_input())
    assert digests_match(digest, digest.upper())
    assert not digests_match(digest, "f" * 64)


def test_permission_snapshot_sorts_roles_and_scopes() -> None:
    snapshot = PermissionSnapshot(
        tenant_id="t", user_id="u", roles=["b", "a"], scopes=["z", "a"]
    )
    rendered = snapshot.as_dict()
    assert rendered["roles"] == ["a", "b"]
    assert rendered["scopes"] == ["a", "z"]
    # Two snapshots with the same grants in different order digest identically.
    one = compute_action_digest(_input(permission_snapshot=snapshot.as_dict()))
    two = compute_action_digest(
        _input(
            permission_snapshot=PermissionSnapshot(
                tenant_id="t", user_id="u", roles=["a", "b"], scopes=["a", "z"]
            ).as_dict()
        )
    )
    assert one == two
