"""Resumable SSE event log over a bounded, expiring Redis Stream.

Every run's progress events live in one stream keyed by run id. The stream is
trimmed to ``max_events`` and given a TTL, so replay memory is bounded and
abandoned runs expire. A client resumes with the stream entry id it last saw
(its ``Last-Event-ID``); if that id has already been trimmed the replay reports
a *gap* so the caller can fall back to a PostgreSQL snapshot rather than skip
milestones silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """One replayed event; ``id`` is the SSE id a client echoes to resume."""

    id: str
    event_type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ReplayResult:
    """Events after the requested id, plus whether earlier events were lost."""

    events: list[StreamEvent]
    gap: bool


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def _id_tuple(entry_id: str) -> tuple[int, int]:
    ms, _, seq = entry_id.partition("-")
    return (int(ms), int(seq or 0))


class RunEventStream:
    """Bounded, TTL'd Redis Stream of a run's SSE events."""

    def __init__(
        self,
        redis: Any,
        *,
        prefix: str = "sse",
        max_events: int = 1000,
        ttl_seconds: int = 3600,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._max_events = max_events
        self._ttl = ttl_seconds

    def _key(self, run_id: str) -> str:
        return f"{self._prefix}:{run_id}"

    async def publish(
        self, run_id: str, event_type: str, data: dict[str, Any]
    ) -> str:
        """Append an event, trim to the bound, refresh the TTL, return its id."""
        key = self._key(run_id)
        entry_id = await self._redis.xadd(
            key,
            {"type": event_type, "data": json.dumps(data, default=str)},
            maxlen=self._max_events,
            approximate=False,
        )
        await self._redis.expire(key, self._ttl)
        return _decode(entry_id)

    async def replay(self, run_id: str, after_id: str = "0") -> ReplayResult:
        """Return events after ``after_id`` and whether earlier ones were trimmed."""
        key = self._key(run_id)
        resume = after_id not in (None, "", "0")
        start = f"({after_id}" if resume else "-"
        raw = await self._redis.xrange(key, min=start, max="+")
        events = [
            StreamEvent(
                id=_decode(entry_id),
                event_type=_decode(fields[b"type" if b"type" in fields else "type"]),
                data=json.loads(
                    _decode(fields[b"data" if b"data" in fields else "data"])
                ),
            )
            for entry_id, fields in raw
        ]
        gap = await self._has_gap(key, after_id) if resume else False
        return ReplayResult(events=events, gap=gap)

    async def _has_gap(self, key: str, after_id: str) -> bool:
        earliest = await self._redis.xrange(key, min="-", max="+", count=1)
        if not earliest:
            # The client held an id but nothing is retained: everything expired.
            return True
        earliest_id = _decode(earliest[0][0])
        # A gap exists when the entry right after `after_id` was already trimmed,
        # i.e. the earliest retained id is newer than the id the client last saw.
        return _id_tuple(earliest_id) > _id_tuple(after_id)

    async def latest_id(self, run_id: str) -> str | None:
        newest = await self._redis.xrevrange(self._key(run_id), count=1)
        return _decode(newest[0][0]) if newest else None

    async def ttl_seconds_remaining(self, run_id: str) -> int | None:
        ttl = await self._redis.ttl(self._key(run_id))
        return int(ttl) if ttl is not None and ttl >= 0 else None
