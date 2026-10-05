"""T103 [US1] a mock reimbursement submission connector. Not a production integration.

This is deliberately and visibly a stand-in: every response carries
``status="mock"`` so no caller, log line or test can mistake it for a real
external submission. It exists so the full approval -> submit -> receipt ->
idempotent-replay path can be exercised end to end without a real expense system,
and so the honest-boundary rule holds -- a mock response always says it is a mock.

What it *does* model faithfully (because those behaviours are what Stage 6 must
prove, not the vendor API):

* **Idempotency with reconciliation.** A repeat of the same idempotency key
  returns the original receipt rather than submitting again. If a prior attempt's
  outcome was never recorded (a crash between "provider accepted" and "we wrote it
  down"), ``reconcile`` is how the connector reports what really happened before a
  retry is allowed -- modelling the real rule that an unknown outcome must be
  reconciled, never blindly re-sent.
* **A stable receipt.** The receipt id is derived from the idempotency key, so a
  reconciliation of the same key always resolves to the same receipt.

What it does NOT do: reach any network, hold any credential, or describe a real
destination. The ``destination`` is an opaque label.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

#: The connector id recorded on every SubmissionJob this connector handles. Part
#: of the unique ``(tenant_id, connector_id, idempotency_key)`` key, so it must be
#: stable.
CONNECTOR_ID = "mock-reimbursement"

#: The marker that makes a mock response unmistakable. Asserted by tests and
#: required by the honest-boundary rule.
MOCK_STATUS = "mock"


@dataclass(frozen=True)
class SubmissionReceipt:
    """What the connector returns for an accepted (mock) submission."""

    status: str
    receipt_id: str
    connector_id: str
    idempotency_key: str
    destination: str
    #: True when this receipt was returned for a key the connector had already
    #: seen, i.e. the request was deduplicated rather than freshly accepted.
    deduplicated: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_mock(self) -> bool:
        return self.status == MOCK_STATUS


@dataclass(frozen=True)
class ReconcileResult:
    """What the connector reports when asked about a possibly-sent key.

    ``accepted`` means the provider has a record for this key (so a retry must not
    re-send); ``unknown`` means it does not, so the caller may proceed to submit.
    """

    status: str
    outcome: str  # "accepted" | "unknown"
    receipt_id: str | None
    idempotency_key: str

    @property
    def already_accepted(self) -> bool:
        return self.outcome == "accepted"


class MockReimbursementConnector:
    """In-memory mock expense-submission connector.

    State lives in a dict rather than a database because this is a *provider*
    stand-in: it models the remote system's own dedup memory. The application's
    durable record of what it asked for is the ``SubmissionJob`` row, which the
    submission service owns.
    """

    connector_id = CONNECTOR_ID

    def __init__(self) -> None:
        self._accepted: dict[tuple[str, str], SubmissionReceipt] = {}

    def _receipt_id(self, *, tenant_id: str, idempotency_key: str) -> str:
        seed = f"{self.connector_id}\x1f{tenant_id}\x1f{idempotency_key}".encode()
        return f"mock-{hashlib.sha256(seed).hexdigest()[:20]}"

    async def submit(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        destination: str,
        payload: dict[str, Any] | None = None,
    ) -> SubmissionReceipt:
        """Accept a (mock) submission, deduplicating by idempotency key.

        The same key always returns the same receipt with ``deduplicated=True`` on
        the second and later calls, which is what lets a duplicate click or client
        retry be at-most-once at the provider boundary too, not only in our DB.
        """
        key = (tenant_id, idempotency_key)
        existing = self._accepted.get(key)
        if existing is not None:
            return SubmissionReceipt(
                status=MOCK_STATUS,
                receipt_id=existing.receipt_id,
                connector_id=self.connector_id,
                idempotency_key=idempotency_key,
                destination=existing.destination,
                deduplicated=True,
                detail={"note": "returned the original receipt; not re-submitted"},
            )
        receipt = SubmissionReceipt(
            status=MOCK_STATUS,
            receipt_id=self._receipt_id(tenant_id=tenant_id, idempotency_key=idempotency_key),
            connector_id=self.connector_id,
            idempotency_key=idempotency_key,
            destination=destination,
            deduplicated=False,
            detail={"note": "mock submission accepted; no external system was contacted"},
        )
        self._accepted[key] = receipt
        return receipt

    async def reconcile(
        self, *, tenant_id: str, idempotency_key: str
    ) -> ReconcileResult:
        """Report whether the provider already has a record for this key.

        Called when a prior attempt's outcome is unknown. ``accepted`` means do not
        re-send (return the known receipt); ``unknown`` means it is safe to submit.
        """
        existing = self._accepted.get((tenant_id, idempotency_key))
        if existing is not None:
            return ReconcileResult(
                status=MOCK_STATUS,
                outcome="accepted",
                receipt_id=existing.receipt_id,
                idempotency_key=idempotency_key,
            )
        return ReconcileResult(
            status=MOCK_STATUS,
            outcome="unknown",
            receipt_id=None,
            idempotency_key=idempotency_key,
        )
