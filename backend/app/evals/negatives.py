"""Negative (should-be-refused) retrieval evaluation items.

A negative item has no gold document on purpose: the corpus does not answer it,
so the correct outcome is that the off-topic gate blocks the retrieved passages
and the turn refuses. Two kinds:

- ``off_topic``: unrelated to enterprise policy at all (weather, code, shopping).
- ``near_miss``: reads like an internal-policy question, but the answer is not in
  the corpus (e.g. 婚假 when only 年假/病假 exist). These are the hard ones — the
  lexical coverage gate usually lets them through.

The marker lives in ``RetrievalEvalItem.relevance_judgement`` (free-form JSON), so
no schema migration is needed, and gold-less negatives can be told apart from
stale items whose gold documents were deleted.
"""

from __future__ import annotations

from typing import Any

NEGATIVE_KINDS = ("off_topic", "near_miss")


def negative_kind(judgement: Any) -> str | None:
    """Return the negative kind for a relevance judgement, or None if positive."""
    if not isinstance(judgement, dict):
        return None
    if not judgement.get("negative"):
        return None
    kind = judgement.get("negative_kind")
    return str(kind) if kind else "unknown"


def is_negative_judgement(judgement: Any) -> bool:
    return negative_kind(judgement) is not None
