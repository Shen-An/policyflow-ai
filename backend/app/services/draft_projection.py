"""T108 [US1] the legacy Draft as a counted, read-only compatibility projection.

Before Stage 6, a proposed edit lived as a free-form ``Draft`` row (``content``,
``source_question``). The authority for a file-workflow edit is now the
``MaterialVersion`` / ``ChangeSet`` pair: an edit is an immutable new version plus
a reviewable change set, not an opaque text blob. The old ``Draft`` surface stays
so pre-Stage-6 chat drafts keep working, but it is a *migration adapter*, and this
module is what makes that honest:

* **every legacy Draft write is counted** by :class:`DraftLegacyTelemetry`, whose
  :meth:`zero_use_over_window` is the Stage 9 condition for deleting the Draft
  path -- the same mechanism ``graph/compat.py`` and ``storage/authority.py`` use;
* **a ChangeSet can be projected into the legacy read shape** so a caller still on
  the old view sees the new authority's data without the new authority having to
  write a Draft row;
* the telemetry records **no draft content** -- only the draft id, tenant and the
  reason it was a legacy write.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DraftLegacyTelemetry",
    "DraftLegacyWrite",
    "legacy_draft_write",
    "project_change_set",
]


@dataclass(frozen=True)
class DraftLegacyWrite:
    """One legacy Draft write. Carries identity and a reason, never content."""

    operation: str
    draft_id: str
    tenant_id: str | None
    reason: str


def legacy_draft_write(
    *, operation: str, draft_id: str, tenant_id: str | None, reason: str
) -> DraftLegacyWrite:
    if operation not in {"create", "update", "confirm", "discard"}:
        raise ValueError(f"unknown legacy draft operation {operation!r}")
    return DraftLegacyWrite(
        operation=operation, draft_id=draft_id, tenant_id=tenant_id, reason=reason
    )


@dataclass
class DraftLegacyTelemetry:
    """Counts legacy Draft writes; drives the Stage 9 removal decision."""

    events: list[DraftLegacyWrite] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def record(self, event: DraftLegacyWrite) -> None:
        self._counts[event.operation] += 1
        self.events.append(event)  # identity + reason only; no draft content

    def usage_count(self, operation: str) -> int:
        return self._counts[operation]

    def total_usage(self) -> int:
        return sum(self._counts.values())

    def zero_use_over_window(self) -> bool:
        """True only when no legacy Draft write has happened -- the removal gate."""
        return self.total_usage() == 0


def project_change_set(change_set: Any, items: list[Any]) -> dict[str, Any]:
    """Render a ChangeSet (the new authority) into the legacy Draft read shape.

    Lets a caller still on the old ``Draft`` view read a Stage-6 change set without
    the change-set path ever writing a Draft row -- the projection is read-only and
    derived, so there is no second authority. Only safe, derived fields appear; no
    object key, host path or sandbox reference.
    """
    return {
        "id": change_set.id,
        "draft_type": "file_change_set",
        "status": _project_state(change_set.state),
        "title": (change_set.summary or "Proposed change")[:255],
        "related_sources": [
            {"path": item.normalized_path, "operation": item.operation}
            for item in items
        ],
        "projection": True,  # marks this as a derived view, not a Draft row
    }


#: ChangeSet states mapped to the legacy Draft status vocabulary, so an old client
#: that only understands draft/confirmed/discarded still sees a sensible status.
_STATE_PROJECTION: dict[str, str] = {
    "draft": "draft",
    "ready": "draft",
    "awaiting_approval": "draft",
    "approved": "confirmed",
    "applied": "confirmed",
    "rejected": "discarded",
    "invalidated": "discarded",
}


def _project_state(state: str) -> str:
    return _STATE_PROJECTION.get(state, "draft")
