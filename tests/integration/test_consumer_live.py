"""T064 / T072 [US2] live-broker consumer: round-trip and kill/redelivery.

These are the real-infrastructure tests the broker-free suites could only simulate.
They need a live RabbitMQ (``broker_url`` skips cleanly otherwise) and start actual
``celery worker`` subprocesses that consume off the broker:

* **round-trip** -- an enqueued durable job, nudged through the real transport, is
  leased, run and completed by a live worker (``job.succeeded``), proving the
  publish -> broker -> consume -> state-machine path end to end.
* **kill / redelivery -> exactly-once** -- a worker is ``kill``-ed (hard, no
  cleanup) while a job is in flight; its lease cannot be released by itself, so a
  second worker reaps the expired lease and re-runs the job. The job still reaches
  exactly one terminal ``succeeded`` state (``attempts == 2``, completed by the
  second worker's pid) and emits exactly one ``job.succeeded`` outbox event -- the
  "重复投递不重复状态变化或副作用" invariant, on real infrastructure.

The durable state lives on a shared SQLite file (the always-available authority
path); PostgreSQL-authoritative concurrency under row locks is covered separately
by ``tests/integration/test_job_lease_pg_concurrency.py``. What is exercised *here*
is the broker <-> consumer <-> idempotent-state-machine round-trip, which is
database-agnostic.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session
from sqlmodel import SQLModel

from backend.app.core.config import Settings
from backend.app.db.models import OutboxEvent, Tenant
from backend.app.db.session import build_async_engine
from backend.app.jobs.celery_app import build_celery_app
from backend.app.jobs.publisher import OutboxPublisher
from backend.app.jobs.service import JobService
from backend.app.jobs.transport import CeleryOutboxTransport

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
PROBE_KIND = "recovery_probe"
QUEUE = "policyflow.default"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_db() -> str:
    """A fresh SQLite file (native path) with the schema and a seeded tenant."""
    path = Path(tempfile.mkdtemp(), "live.db").resolve().as_posix()
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, code="acme", name="Acme"))
        session.commit()
    engine.dispose()
    return url


def _start_worker(db_url: str, broker_url: str, *, lease_seconds: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env["CELERY_BROKER_URL"] = broker_url
    env["POLICYFLOW_WORKER_LEASE_SECONDS"] = str(lease_seconds)
    return subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "backend.app.jobs.worker", "worker",
            "--pool=solo", "-Q", QUEUE, "--concurrency=1", "--loglevel=warning",
            "--without-gossip", "--without-mingle", "--without-heartbeat",
        ],
        env=env, cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _kill(proc: subprocess.Popen) -> None:
    """Hard-kill a worker process tree (no graceful shutdown -- the kill -9 case)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:  # pragma: no cover - CI runs on Windows here
        proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _wait_consumers(app, count: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with app.connection_for_write() as conn:
                _, _, consumers = conn.default_channel.queue_declare(
                    queue=QUEUE, passive=True
                )
            if consumers >= count:
                return True
        except Exception:  # noqa: BLE001 - queue may not exist until a consumer binds
            pass
        time.sleep(0.5)
    return False


async def _wait_state(svc: JobService, job_id: str, states: set[str], timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = await svc.get(job_id)
        if job is not None and job.state in states:
            return job
        time.sleep(0.25)
    return await svc.get(job_id)


async def _nudge(app, job_id: str) -> None:
    await CeleryOutboxTransport(app).publish(
        {"aggregate_id": job_id, "aggregate_type": "durable_job",
         "event_type": "job.enqueued", "payload": {}}
    )


async def _succeeded_events(engine, job_id: str) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        result = await session.execute(
            select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.aggregate_id == job_id,
                OutboxEvent.event_type == "job.succeeded",
            )
        )
        return int(result.scalar_one())


async def test_live_round_trip_consumes_and_completes(broker_url) -> None:
    db_url = _make_db()
    app = build_celery_app(Settings(CELERY_BROKER_URL=broker_url, _env_file=None))
    engine = build_async_engine(db_url)
    svc = JobService(factory=async_sessionmaker(engine, expire_on_commit=False))
    worker = _start_worker(db_url, broker_url, lease_seconds=30)
    try:
        assert _wait_consumers(app, 1), "worker never began consuming the queue"
        job = await svc.enqueue(
            tenant_id=TENANT, kind=PROBE_KIND, payload={"token": "rt"},
            idempotency_key="rt", max_attempts=3,
        )
        await _nudge(app, job.id)
        done = await _wait_state(svc, job.id, {"succeeded", "terminal_failed"}, 30)
        assert done is not None and done.state == "succeeded"
        assert done.result_ref == f"probe-ok:pid={worker.pid}"
        assert await _succeeded_events(engine, job.id) == 1
    finally:
        _kill(worker)
        await engine.dispose()


async def test_kill_mid_flight_redelivers_and_stays_exactly_once(broker_url) -> None:
    db_url = _make_db()
    app = build_celery_app(Settings(CELERY_BROKER_URL=broker_url, _env_file=None))
    engine = build_async_engine(db_url)
    svc = JobService(factory=async_sessionmaker(engine, expire_on_commit=False))
    # Short lease so the killed worker's lease is reclaimable quickly; a probe that
    # sleeps long enough to be killed while it is still running.
    worker_a = _start_worker(db_url, broker_url, lease_seconds=2)
    worker_b = None
    try:
        assert _wait_consumers(app, 1), "worker A never began consuming"
        job = await svc.enqueue(
            tenant_id=TENANT, kind=PROBE_KIND, payload={"sleep_ms": 4000},
            idempotency_key="kill", max_attempts=3,
        )
        await _nudge(app, job.id)
        # Wait until A has actually leased + started the job (state running).
        running = await _wait_state(svc, job.id, {"running"}, 15)
        assert running is not None and running.state == "running"
        assert running.lease_owner and running.lease_owner.endswith(str(worker_a.pid))

        _kill(worker_a)  # hard kill mid-flight: no lease release, message redelivers

        # A fresh worker; by the time it boots, A's 2s lease has expired and is
        # reclaimable. Nudge it: it reaps A's lease, re-runs, and completes once.
        worker_b = _start_worker(db_url, broker_url, lease_seconds=30)
        assert _wait_consumers(app, 1), "worker B never began consuming"
        await _nudge(app, job.id)
        done = await _wait_state(svc, job.id, {"succeeded", "terminal_failed"}, 40)

        assert done is not None and done.state == "succeeded", f"ended {done and done.state}"
        assert done.attempts == 2, f"expected 2 attempts, got {done.attempts}"
        assert done.result_ref == f"probe-ok:pid={worker_b.pid}"  # B finished it, not A
        assert await _succeeded_events(engine, job.id) == 1  # exactly one terminal event
    finally:
        _kill(worker_a)
        if worker_b is not None:
            _kill(worker_b)
        await engine.dispose()


async def test_live_cancel_mid_flight_finalizes_cancelled(broker_url) -> None:
    db_url = _make_db()
    app = build_celery_app(Settings(CELERY_BROKER_URL=broker_url, _env_file=None))
    engine = build_async_engine(db_url)
    svc = JobService(factory=async_sessionmaker(engine, expire_on_commit=False))
    # Long lease so the job is not reaped mid-run -- we want a cooperative cancel,
    # not a lease expiry. The worker polls cancel_requested at its bounded step.
    worker = _start_worker(db_url, broker_url, lease_seconds=60)
    try:
        assert _wait_consumers(app, 1), "worker never began consuming"
        job = await svc.enqueue(
            tenant_id=TENANT, kind=PROBE_KIND, payload={"sleep_ms": 10000},
            idempotency_key="live-cancel", max_attempts=3,
        )
        await _nudge(app, job.id)
        running = await _wait_state(svc, job.id, {"running"}, 20)
        assert running is not None and running.state == "running"

        await svc.request_cancel(job_id=job.id)  # cooperative cancel while in flight
        done = await _wait_state(
            svc, job.id, {"cancelled", "succeeded", "terminal_failed"}, 20
        )
        assert done is not None and done.state == "cancelled", f"ended {done and done.state}"
    finally:
        _kill(worker)
        await engine.dispose()


async def test_live_full_outbox_relay_round_trip(broker_url) -> None:
    # The full production path: enqueue writes a job.enqueued outbox row; the real
    # OutboxPublisher relay (not a hand-published nudge) claims it and publishes
    # through the real AMQP transport; a live worker consumes and completes it.
    db_url = _make_db()
    app = build_celery_app(Settings(CELERY_BROKER_URL=broker_url, _env_file=None))
    engine = build_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = JobService(factory=factory)
    worker = _start_worker(db_url, broker_url, lease_seconds=30)
    try:
        assert _wait_consumers(app, 1), "worker never began consuming"
        job = await svc.enqueue(
            tenant_id=TENANT, kind=PROBE_KIND, payload={"token": "relay"},
            idempotency_key="relay-rt", max_attempts=3,
        )
        publisher = OutboxPublisher(factory=factory, transport=CeleryOutboxTransport(app))
        delivered = await publisher.relay_once()
        assert delivered >= 1, "relay published nothing"

        done = await _wait_state(svc, job.id, {"succeeded", "terminal_failed"}, 30)
        assert done is not None and done.state == "succeeded"
        assert done.result_ref == f"probe-ok:pid={worker.pid}"
    finally:
        _kill(worker)
        await engine.dispose()
