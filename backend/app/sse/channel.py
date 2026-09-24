"""Bounded per-connection SSE channel with heartbeats and backpressure.

Each SSE connection drains one bounded in-process channel. The bound is the
whole point: if a consumer cannot keep up, ``publish`` fails with
:class:`SlowConsumer` once the send timeout elapses instead of buffering without
limit, and the server drops that connection. When no events flow, the
subscription yields a heartbeat so intermediaries do not idle-close the stream.
Closing the channel ends every subscription promptly, which is how a
disconnected connection's resources are reclaimed within the budget.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio


class SlowConsumer(RuntimeError):
    """The consumer did not drain within the send timeout (backpressure)."""


class ChannelClosed(RuntimeError):
    """The channel was closed; no further events may be published."""


@dataclass
class ChannelEvent:
    """One SSE payload, or a heartbeat sentinel when ``is_heartbeat``."""

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    is_heartbeat: bool = False


def _heartbeat() -> ChannelEvent:
    return ChannelEvent(event_type="heartbeat", is_heartbeat=True)


class BoundedEventChannel:
    """A bounded fan-out to a single SSE subscriber.

    ``capacity`` is the buffered-event bound; ``heartbeat_seconds`` is the idle
    interval after which :meth:`subscribe` emits a heartbeat.
    """

    def __init__(self, *, capacity: int, heartbeat_seconds: float) -> None:
        self._send, self._receive = anyio.create_memory_object_stream[ChannelEvent](
            max_buffer_size=capacity
        )
        self._heartbeat_seconds = heartbeat_seconds
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def publish(self, event: ChannelEvent, *, timeout: float) -> None:
        """Enqueue an event, or raise if the consumer is too slow / closed."""
        if self._closed:
            raise ChannelClosed("channel is closed")
        with anyio.move_on_after(timeout) as scope:
            await self._send.send(event)
        if scope.cancelled_caught:
            raise SlowConsumer(
                f"consumer did not drain within {timeout}s; dropping connection"
            )

    async def subscribe(self) -> AsyncIterator[ChannelEvent]:
        """Yield events as they arrive, injecting heartbeats while idle.

        Ends when the channel is closed, which is how the connection's task
        unwinds and its resources are released.
        """
        while True:
            try:
                with anyio.fail_after(self._heartbeat_seconds):
                    event = await self._receive.receive()
            except TimeoutError:
                yield _heartbeat()
                continue
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                # Channel closed (from either end): end the subscription so the
                # connection's task unwinds and its resources are released.
                return
            yield event

    async def aclose(self) -> None:
        """Close both ends, ending any active subscription."""
        if self._closed:
            return
        self._closed = True
        await self._send.aclose()
        await self._receive.aclose()
