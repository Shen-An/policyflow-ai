"""Run-facing SSE event vocabulary shared by the producer (worker) and the
consumer (the ``/runs/{id}/events`` live tail).

These are the event *types* a run's progress stream carries, distinct from the
internal ``job.*`` outbox event names: the SSE surface speaks about the *run*. A
worker publishes these to the run's :class:`~backend.app.sse.stream.RunEventStream`
as the durable job advances; a connected client tailing that stream renders them
and, on a terminal type, the live tail returns so the connection is released.
"""

from __future__ import annotations

from typing import Protocol

RUN_STARTED = "run.started"
RUN_PROGRESS = "run.progress"
RUN_SUCCEEDED = "run.succeeded"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"

#: Types that mean the run is finished -- the live tail stops after one of these.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {RUN_SUCCEEDED, RUN_FAILED, RUN_CANCELLED}
)

#: Maps a durable-job terminal state to its run-facing SSE event type.
JOB_STATE_TO_RUN_EVENT: dict[str, str] = {
    "succeeded": RUN_SUCCEEDED,
    "terminal_failed": RUN_FAILED,
    "cancelled": RUN_CANCELLED,
}


class _HasEventType(Protocol):
    event_type: str


def is_terminal_event(event: _HasEventType) -> bool:
    """True when an event means the run has reached a terminal state."""
    return event.event_type in TERMINAL_EVENT_TYPES
