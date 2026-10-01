"""Production binding of the ``/api/v2/runs`` admission gate to Redis (T069×T066).

The runs route declares a :class:`~backend.app.api.routes_runs.RunAdmission`
protocol and defaults it to allow-all; this module is the real binding that
closes that documented gap. :class:`CoordinatorAdmission` adapts a live
:class:`~backend.app.quota.coordinator.QuotaCoordinator` (Redis token bucket +
lease semaphore, enforced atomically in Lua) to that protocol, so an overloaded
submission edge returns ``429`` (rate) / ``503`` (concurrency) + ``Retry-After``
over the *same* cross-instance-safe path a multi-node deployment shares -- not a
per-process approximation.

Honest boundaries (see docs/08 §10 / §12):

* **Lease lifetime.** An admitted request acquires a concurrency lease and does
  **not** release it here. Explicit release on run-terminal belongs to the
  worker loop (T064), which is broker-gated. Until that lands, the slot is
  reclaimed by its TTL (``lease_ms``): conservative -- a slot is never leaked,
  though a short run may over-reserve until expiry. This is exactly the
  crash-safe reclaim mode the coordinator is built around; a lease expiring is
  not, on its own, permission to duplicate a side effect (that stays with the
  durable job outbox).
* **Policy source.** Per-``(resource, identity)`` limits come from
  ``limits_provider``. The production provider resolves a
  :class:`~backend.app.db.models.QuotaPolicy` via
  :meth:`~backend.app.quota.ledger.QuotaLedger.resolve_policy` (PostgreSQL
  authority) and is wired separately when PG is reachable; this adapter stays
  agnostic so it is testable on Redis alone.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.app.api.routes_runs import AdmissionOutcome
from backend.app.quota.coordinator import QuotaCoordinator

#: Maps a coordinator decline reason to the stable API error code the route
#: surfaces as ``error.code`` (kept in lockstep with the T069 contract tests).
_REASON_TO_CODE: dict[str, str] = {
    "rate_limited": "RATE_LIMITED",
    "concurrency_saturated": "CONCURRENCY_SATURATED",
}


@dataclass(frozen=True)
class QuotaLimits:
    """Resolved budget for one ``(resource, identity)`` admission check.

    ``capacity``/``refill_per_sec`` size the token bucket; ``max_concurrency``/
    ``lease_ms`` size the lease semaphore. ``cost`` is the token draw per unit of
    admitted work (default one).
    """

    capacity: float
    refill_per_sec: float
    max_concurrency: int
    lease_ms: int
    cost: float = 1.0


#: Returns the limits for a ``(resource, identity)``. May be sync or async so a
#: production provider can hit the PG ledger while a test provides a constant.
LimitsProvider = Callable[[str, str], QuotaLimits | Awaitable[QuotaLimits]]


class CoordinatorAdmission:
    """Bind :class:`RunAdmission` to a live :class:`QuotaCoordinator`.

    Wire an instance onto ``app.state.run_admission`` when a reachable Redis is
    configured; otherwise the route keeps its allow-all default.
    """

    def __init__(
        self,
        coordinator: QuotaCoordinator,
        *,
        limits_provider: LimitsProvider,
    ) -> None:
        self._coordinator = coordinator
        self._limits_provider = limits_provider

    async def admit(self, *, resource: str, identity: str) -> AdmissionOutcome:
        """Admit, or decline with the HTTP shape the route maps 1:1 to a response.

        Never raises for a decline -- the coordinator's :class:`QuotaDecision` is
        translated into an :class:`AdmissionOutcome`, carrying the ``429``/``503``
        status, the advisory ``retry_after_seconds`` and the stable error code.
        """
        limits = self._limits_provider(resource, identity)
        if inspect.isawaitable(limits):
            limits = await limits

        decision = await self._coordinator.admit(
            resource=resource,
            identity=identity,
            capacity=limits.capacity,
            refill_per_sec=limits.refill_per_sec,
            max_concurrency=limits.max_concurrency,
            lease_ms=limits.lease_ms,
            cost=limits.cost,
        )
        if decision.admitted:
            # The lease is intentionally held (TTL-reclaimed); see module docstring.
            return AdmissionOutcome(admitted=True)

        reason = decision.reason or "rate_limited"
        return AdmissionOutcome(
            admitted=False,
            reason=reason,
            http_status=decision.http_status,
            error_code=_REASON_TO_CODE.get(reason, "RATE_LIMITED"),
            retry_after_seconds=decision.retry_after_seconds,
        )
