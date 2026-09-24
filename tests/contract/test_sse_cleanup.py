"""T060 [US2] contract for the bounded SSE fan-out channel.

Each SSE connection is fed by a bounded in-process channel. The bound is the
backpressure mechanism: a consumer too slow to drain within the send timeout is
detected (``SlowConsumer``) so the server can drop it and reclaim resources
rather than buffering without limit. When the channel is idle a heartbeat is
emitted so proxies do not idle-close the connection, and closing the channel
ends every subscription promptly (cleanup within the disconnect budget).

Pure asyncio — no external infrastructure required.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.sse.channel import (
    BoundedEventChannel,
    ChannelClosed,
    ChannelEvent,
    SlowConsumer,
)


async def test_delivers_events_in_order() -> None:
    ch = BoundedEventChannel(capacity=8, heartbeat_seconds=10.0)
    gen = ch.subscribe()
    await ch.publish(ChannelEvent(event_type="a", data={"i": 1}), timeout=1.0)
    await ch.publish(ChannelEvent(event_type="b", data={"i": 2}), timeout=1.0)
    first = await asyncio.wait_for(gen.__anext__(), 1.0)
    second = await asyncio.wait_for(gen.__anext__(), 1.0)
    assert (first.event_type, first.data["i"]) == ("a", 1)
    assert (second.event_type, second.data["i"]) == ("b", 2)
    await ch.aclose()


async def test_backpressure_raises_on_slow_consumer() -> None:
    # No reader; capacity 2 fills, the third publish cannot drain in time.
    ch = BoundedEventChannel(capacity=2, heartbeat_seconds=10.0)
    await ch.publish(ChannelEvent(event_type="x", data={}), timeout=0.5)
    await ch.publish(ChannelEvent(event_type="x", data={}), timeout=0.5)
    with pytest.raises(SlowConsumer):
        await ch.publish(ChannelEvent(event_type="x", data={}), timeout=0.2)
    await ch.aclose()


async def test_heartbeat_emitted_when_idle() -> None:
    ch = BoundedEventChannel(capacity=8, heartbeat_seconds=0.05)
    gen = ch.subscribe()
    beat = await asyncio.wait_for(gen.__anext__(), 1.0)
    assert beat.is_heartbeat
    await ch.aclose()


async def test_close_ends_subscription_promptly() -> None:
    ch = BoundedEventChannel(capacity=8, heartbeat_seconds=10.0)
    gen = ch.subscribe()
    await ch.publish(ChannelEvent(event_type="a", data={}), timeout=1.0)
    got = await asyncio.wait_for(gen.__anext__(), 1.0)
    assert not got.is_heartbeat
    await ch.aclose()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), 1.0)
    assert ch.closed


async def test_publish_after_close_is_rejected() -> None:
    ch = BoundedEventChannel(capacity=8, heartbeat_seconds=10.0)
    await ch.aclose()
    with pytest.raises(ChannelClosed):
        await ch.publish(ChannelEvent(event_type="a", data={}), timeout=1.0)
