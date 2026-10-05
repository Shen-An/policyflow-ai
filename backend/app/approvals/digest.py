"""T101 [US1] the action digest: the exact thing an approval is a decision about.

An approval is only safe if "the thing approved" and "the thing executed" are
provably identical. The action digest is that proof: a SHA-256 over every input
``data-model.md`` says binds an approval -- the action, the destination, the exact
source and output material versions, their content hashes, the diff, the evidence
set, the permission snapshot and the declared side effects. If any of those
changes, the digest changes, and the prior approval no longer matches, so it
cannot be reused for a different action.

Two properties the implementation guarantees:

* **Canonical and order-independent.** The inputs are normalised (sets sorted,
  mappings key-sorted) before hashing, so the same logical action always produces
  the same digest regardless of dict ordering, list ordering of versions, or JSON
  whitespace. Without this, a cosmetic reserialisation would spuriously
  invalidate a valid approval.
* **Total.** Every field in :data:`~backend.app.db.models.APPROVAL_DIGEST_INPUTS`
  is covered, checked at construction. A field that the model says binds an
  approval but the digest forgot would be a hole: that input could change under a
  valid approval without anyone noticing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.app.db.models import APPROVAL_DIGEST_INPUTS

#: Version of the digest construction. Mixed into the hash so that if the set of
#: bound inputs ever changes, old and new digests are provably distinct rather
#: than silently colliding -- an approval minted under the old rules can never
#: match an action evaluated under the new ones.
DIGEST_SCHEMA_VERSION = "action-digest/v1"


@dataclass(frozen=True)
class ActionDigestInput:
    """Everything an approval decision is bound to.

    The field names mirror :data:`APPROVAL_DIGEST_INPUTS` exactly; the
    cross-check in :func:`compute_action_digest` fails loudly if they drift, so a
    new bound input cannot be added to the model without being added here.
    """

    action: str
    destination: str
    source_versions: Sequence[str]
    output_versions: Sequence[str]
    file_hashes: Mapping[str, str]
    diff: str
    evidence_set: Sequence[str]
    permission_snapshot: Mapping[str, Any]
    side_effects: Sequence[str]

    def canonical(self) -> dict[str, Any]:
        """Return the normalised, order-independent form that gets hashed."""
        return {
            "action": self.action,
            "destination": self.destination,
            # Versions and evidence are unordered sets of ids; sort so a reordering
            # of the same set does not change the digest.
            "source_versions": sorted(self.source_versions),
            "output_versions": sorted(self.output_versions),
            # File hashes are keyed by path; sort by key for determinism.
            "file_hashes": {key: self.file_hashes[key] for key in sorted(self.file_hashes)},
            "diff": _hash_text(self.diff),
            "evidence_set": sorted(self.evidence_set),
            "permission_snapshot": _canonical(self.permission_snapshot),
            "side_effects": sorted(self.side_effects),
        }


@dataclass(frozen=True)
class PermissionSnapshot:
    """The authorization context an approval was requested under.

    Captured for *explanation* and for digest binding; the live authorization is
    still rechecked at execution time (``data-model.md``). It records the
    approver-facing identity and the exact grant set, so a permission change
    between request and execution changes the digest and invalidates the approval.
    """

    tenant_id: str
    user_id: str
    roles: Sequence[str] = field(default_factory=tuple)
    scopes: Sequence[str] = field(default_factory=tuple)
    authorization_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "roles": sorted(self.roles),
            "scopes": sorted(self.scopes),
            "authorization_version": self.authorization_version,
        }


def compute_action_digest(payload: ActionDigestInput) -> str:
    """Return the stable SHA-256 hex digest for one action.

    Raises if the digest does not cover every bound input, so a change to
    :data:`APPROVAL_DIGEST_INPUTS` that was not reflected here fails fast rather
    than quietly leaving an input unbound.
    """
    canonical = payload.canonical()
    missing = APPROVAL_DIGEST_INPUTS - set(canonical)
    if missing:
        raise ValueError(
            f"the action digest does not cover every bound input; missing {sorted(missing)}"
        )
    extra = set(canonical) - APPROVAL_DIGEST_INPUTS
    if extra:
        raise ValueError(
            f"the action digest covers inputs the model does not declare: {sorted(extra)}"
        )
    body = {"schema": DIGEST_SCHEMA_VERSION, "inputs": canonical}
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def digests_match(expected: str, actual: str) -> bool:
    """Constant-time comparison of two digests.

    An approval check compares a stored digest with a freshly computed one;
    ``hmac.compare_digest`` avoids leaking where they first differ. It also
    normalises case so a lowercase-hex contract value matches regardless of how
    the caller cased it.
    """
    import hmac

    return hmac.compare_digest(expected.lower(), actual.lower())


def _canonical(value: Any) -> Any:
    """Recursively sort mappings and leave scalars/sequences order-preserving.

    Mappings are sorted by key (order-insensitive); lists are left as-is because
    for the permission snapshot their order is not semantically meaningful but
    stable from the caller. The caller passes already-sorted role/scope lists.
    """
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _hash_text(text: str) -> str:
    """Hash free text (the diff) rather than embed it, keeping the digest bounded.

    A multi-megabyte diff should not inflate every digest comparison; its content
    still fully determines the result because any change to the diff changes this
    hash.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
