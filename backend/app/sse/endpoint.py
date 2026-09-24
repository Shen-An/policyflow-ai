"""T068 [US2] SSE HTTP surface: replay-then-live with gap -> snapshot signalling.

This composes the two Stage-4 SSE primitives into the shape an HTTP handler
streams:

* :class:`~backend.app.sse.stream.RunEventStream` (Redis Streams) supplies the
  *replay* -- every event the run has emitted since the client's
  ``Last-Event-ID``. If the requested id was already trimmed, replay reports a
  *gap* and this layer emits a single ``snapshot_required`` control frame and then
  replays the authoritative durable milestones read from PostgreSQL (via a
  ``snapshot`` reader, :class:`~backend.app.sse.snapshot.DurableRunSnapshot`), so
  recovery survives a full Redis flush instead of silently skipping milestones.
  Without a durable reader wired it falls back to the retained Redis events.
* :class:`~backend.app.sse.channel.BoundedEventChannel` supplies the *live* tail
  once replay is drained: bounded, heartbeated, and closed to release the
  connection's resources.

:func:`sse_event_source` yields sse-starlette-compatible mappings. With no live
channel it is *replay-only* and returns once the backlog is sent (the mode an
HTTP catch-up request uses and the mode this repo can verify end-to-end against
a real Redis without a running worker). The live-tail path is exercised at the
generator level; wiring a producer that fans run events into per-connection
channels belongs with the Celery consumers (T064) and is gated on the broker.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any

from backend.app.sse.channel import BoundedEventChannel
from backend.app.sse.stream import RunEventStream, StreamEvent

#: Control frame telling the client its resume point was trimmed; it must reload
#: authoritative state from the PostgreSQL snapshot before trusting live events.
CONTROL_SNAPSHOT_REQUIRED = "snapshot_required"
#: Idle keep-alive frame so intermediaries do not close the stream.
CONTROL_HEARTBEAT = "heartbeat"


def _frame(event_type: str, data: Any, event_id: str | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "event": event_type,
        "data": json.dumps(data, default=str) if not isinstance(data, str) else data,
    }
    if event_id:
        frame["id"] = event_id
    return frame


async def sse_event_source(
    *,
    stream: RunEventStream,
    run_id: str,
    last_event_id: str | None = "0",
    channel: BoundedEventChannel | None = None,
    snapshot: Callable[[], Awaitable[Sequence[StreamEvent]]] | None = None,
) -> AsyncIterator[Mapping[str, Any]]:
    """Yield SSE frames for ``run_id``: replay backlog, then optionally live tail.

    ``last_event_id`` is the client's ``Last-Event-ID`` (``"0"`` or ``None`` from
    the start). A trimmed resume point yields one ``snapshot_required`` frame. When
    ``snapshot`` is supplied (the production wiring), the gap is then filled from
    the authoritative PostgreSQL durable milestones -- so recovery survives a full
    Redis flush -- and the partial Redis tail is *not* also emitted, since the
    durable snapshot is the superset authority. When ``snapshot`` is ``None`` the
    generator falls back to the retained Redis events. When ``channel`` is ``None``
    the generator ends after replay; otherwise it drains the channel until it is
    closed, passing heartbeats through, at which point it returns so the connection
    is released.
    """
    after = last_event_id or "0"
    result = await stream.replay(run_id, after_id=after)
    if result.gap:
        yield _frame(CONTROL_SNAPSHOT_REQUIRED, {"run_id": run_id})
        if snapshot is not None:
            for event in await snapshot():
                yield _frame(event.event_type, event.data, event.id)
        else:
            for event in result.events:
                yield _frame(event.event_type, event.data, event.id)
    else:
        for event in result.events:
            yield _frame(event.event_type, event.data, event.id)

    if channel is None:
        return

    async for live in channel.subscribe():
        if live.is_heartbeat:
            yield _frame(CONTROL_HEARTBEAT, "")
            continue
        yield _frame(live.event_type, live.data, live.id)
