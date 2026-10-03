"""T062 [US2] retry backoff jitter.

In this architecture Celery's native autoretry is not used -- the durable-job
state machine owns retries -- so the retry timer that matters is the re-queue
backoff in :meth:`JobService.fail`. These tests pin the jitter that spreads that
backoff so a batch of jobs failing together (a provider blip, or redelivery after
a broker reconnect) does not retry in lockstep:

- with jitter off (default) the backoff is exactly the deterministic exponential
  the recovery contracts rely on;
- with jitter on, the backoff stays within ``[base*(1-j), base*(1+j)]`` and varies
  across jobs (a seeded rng makes the assertion deterministic).
"""

from __future__ import annotations

import random

from backend.app.jobs.service import JobService

TENANT = "11111111-1111-1111-1111-111111111111"


def _svc(jobs, **kw) -> JobService:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.db.session import build_async_engine

    engine = build_async_engine(jobs.url)
    return JobService(factory=async_sessionmaker(engine, expire_on_commit=False), **kw)


def test_backoff_without_jitter_is_deterministic_exponential(jobs) -> None:
    svc = _svc(jobs, backoff_base_seconds=2.0, max_backoff_seconds=600.0)
    # attempts=1 -> base*2**0; attempts=2 -> base*2**1; attempts=3 -> base*2**2
    assert svc._backoff_seconds(1) == 2.0
    assert svc._backoff_seconds(2) == 4.0
    assert svc._backoff_seconds(3) == 8.0


def test_backoff_with_jitter_stays_in_band_and_varies(jobs) -> None:
    jitter = 0.25
    svc = _svc(
        jobs, backoff_base_seconds=10.0, max_backoff_seconds=600.0,
        backoff_jitter=jitter, rng=random.Random(12345),
    )
    base = 10.0  # attempts=1
    samples = [svc._backoff_seconds(1) for _ in range(50)]
    assert all(base * (1 - jitter) <= s <= base * (1 + jitter) for s in samples)
    assert len(set(samples)) > 1  # jitter actually varies the delay


def test_jitter_never_exceeds_max_backoff(jobs) -> None:
    svc = _svc(
        jobs, backoff_base_seconds=500.0, max_backoff_seconds=600.0,
        backoff_jitter=1.0, rng=random.Random(7),
    )
    # base at attempts=1 is 500; +100% jitter would reach 1000, must clamp to 600.
    assert all(svc._backoff_seconds(1) <= 600.0 for _ in range(100))
    assert all(svc._backoff_seconds(1) >= 0.0 for _ in range(100))
