"""Redis-coordinated quota admission: token bucket + lease semaphore.

Both limits are enforced by Lua scripts, which Redis runs atomically on a single
thread, so a check-and-consume can never interleave across instances and
over-admit. The coordinator returns a typed :class:`QuotaDecision` that the API
maps to ``200`` / ``429`` (rate) / ``503`` (concurrency) with ``Retry-After``.

Concurrency is a *lease*, not a counter: a holder that crashes without releasing
does not leak its slot, because every acquire first purges leases whose expiry
has passed. A lease expiring is not, on its own, permission to duplicate a
non-idempotent side effect — that guarantee belongs to the durable job outbox.

Time is injected as ``now_ms`` on every call so refill and expiry are testable
without wall-clock waits; production passes :meth:`_now_ms`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

# Token bucket: refill by elapsed time, consume ``cost`` if available.
# Returns {allowed, tokens*1000 (int), retry_after_ms}.
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + (elapsed / 1000.0) * refill)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  local deficit = cost - tokens
  if refill > 0 then retry = math.ceil(deficit / refill * 1000.0) else retry = ttl end
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
if ttl > 0 then redis.call('PEXPIRE', key, ttl) end
return {allowed, math.floor(tokens * 1000), retry}
"""

# Lease semaphore: purge expired leases, admit if under max, else report the
# soonest expiry as retry_after. Returns {acquired, remaining, retry_after_ms}.
_ACQUIRE_SLOT_LUA = """
local key = KEYS[1]
local maxc = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local lease = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local n = redis.call('ZCARD', key)
if n < maxc then
  redis.call('ZADD', key, now + lease, member)
  redis.call('PEXPIRE', key, lease + 1000)
  return {1, maxc - n - 1, 0}
end
local soonest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry = 0
if soonest[2] then retry = tonumber(soonest[2]) - now end
if retry < 0 then retry = 0 end
return {0, 0, retry}
"""

_RELEASE_SLOT_LUA = "return redis.call('ZREM', KEYS[1], ARGV[1])"


@dataclass(frozen=True)
class QuotaDecision:
    """Outcome of an admission attempt, ready to shape an HTTP response."""

    admitted: bool
    reason: str | None  # None | "rate_limited" | "concurrency_saturated"
    http_status: int  # 200 | 429 | 503
    retry_after_seconds: float
    lease_id: str | None
    remaining_tokens: float


class QuotaCoordinator:
    """Atomic admission control over a shared Redis.

    ``prefix`` namespaces every key so tenants, workloads and tests never collide.
    """

    def __init__(self, redis: Any, *, prefix: str = "policyflow:quota") -> None:
        self._redis = redis
        self._prefix = prefix
        self._consume = redis.register_script(_TOKEN_BUCKET_LUA)
        self._acquire = redis.register_script(_ACQUIRE_SLOT_LUA)
        self._release_script = redis.register_script(_RELEASE_SLOT_LUA)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _tb_key(self, resource: str, identity: str) -> str:
        return f"{self._prefix}:tb:{resource}:{identity}"

    def _sem_key(self, resource: str, identity: str) -> str:
        return f"{self._prefix}:sem:{resource}:{identity}"

    async def admit(
        self,
        *,
        resource: str,
        identity: str,
        capacity: float,
        refill_per_sec: float,
        max_concurrency: int,
        lease_ms: int,
        cost: float = 1.0,
        now_ms: int | None = None,
    ) -> QuotaDecision:
        """Try to admit one unit of work under both the rate and concurrency limits.

        The concurrency slot is acquired first because it is reversible: if the
        rate check then denies admission, the slot is released so a rate-limited
        request never ties up concurrency.
        """
        now = self._now_ms() if now_ms is None else now_ms
        sem_key = self._sem_key(resource, identity)
        tb_key = self._tb_key(resource, identity)
        lease_id = uuid.uuid4().hex

        acquired, _remaining, sem_retry = await self._acquire(
            keys=[sem_key], args=[max_concurrency, now, lease_ms, lease_id]
        )
        if not int(acquired):
            return QuotaDecision(
                admitted=False,
                reason="concurrency_saturated",
                http_status=503,
                retry_after_seconds=int(sem_retry) / 1000.0,
                lease_id=None,
                remaining_tokens=0.0,
            )

        allowed, tokens_milli, tb_retry = await self._consume(
            keys=[tb_key], args=[capacity, refill_per_sec, now, cost, lease_ms]
        )
        if not int(allowed):
            await self._release_script(keys=[sem_key], args=[lease_id])
            return QuotaDecision(
                admitted=False,
                reason="rate_limited",
                http_status=429,
                retry_after_seconds=int(tb_retry) / 1000.0,
                lease_id=None,
                remaining_tokens=int(tokens_milli) / 1000.0,
            )

        return QuotaDecision(
            admitted=True,
            reason=None,
            http_status=200,
            retry_after_seconds=0.0,
            lease_id=lease_id,
            remaining_tokens=int(tokens_milli) / 1000.0,
        )

    async def release(self, *, resource: str, identity: str, lease_id: str) -> bool:
        """Release a held concurrency lease. Idempotent: unknown ids return False."""
        removed = await self._release_script(
            keys=[self._sem_key(resource, identity)], args=[lease_id]
        )
        return int(removed) == 1

    async def active_slots(
        self, *, resource: str, identity: str, now_ms: int | None = None
    ) -> int:
        """Live concurrency holders after purging expired leases."""
        now = self._now_ms() if now_ms is None else now_ms
        key = self._sem_key(resource, identity)
        await self._redis.zremrangebyscore(key, "-inf", now)
        return int(await self._redis.zcard(key))
