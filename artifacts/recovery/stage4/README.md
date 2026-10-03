# Stage-4 Recovery & Capacity Evidence (Phase 4 / User Story 2)

**Scope:** honest, reproducible evidence for Phase 4「大规模并发下稳定使用」(T055–T072).
This directory records what was exercised against **real infrastructure** and names
precisely what remains, so nothing here overstates. The Checkpoint is **not**
declared (see the assessment at the end).

## Infrastructure (all live during the latest capture — see `pytest-t072-stage4.txt`)

| Component  | State | Brought up how |
|------------|-------|----------------|
| PostgreSQL | 17.10 @ `127.0.0.1:55432` | local `.pgdata` via conda `pg_ctl` |
| Redis      | @ `127.0.0.1:6379` | pre-existing |
| RabbitMQ   | 3.13.7 @ `127.0.0.1:5672` | Docker (`policyflow-rabbit`, image via `docker.m.daocloud.io` mirror; no daemon.json change) |

## Evidence files

- `infra-probe.txt` — PG/Redis up, (earlier) RabbitMQ down snapshot.
- `broker-topology-live.txt` — T062 quorum topology declared + verified via `rabbitmqctl` on the live broker.
- `pytest-broker-stage4.txt` — the 15 live-broker-path tests (transport / consumer cycle+cancel / relay-loop / live round-trip / kill-redelivery→exactly-once / full-relay / lifespan wiring).
- `pytest-pg-stage4.txt` — PG-authoritative suites (R27): integration 28 + contract 99 + recovery 12 = 139 passed on real PG+Redis.
- `pytest-t072-stage4.txt` — consolidated T072 capture: recovery 21 + SSE/quota 49 + live-broker 9 + capacity 3 = **82 passed** on live infra (PG-concurrency referenced from R27; see its footer).
- `capacity-saturation.json` / `capacity-sse-cleanup.json` / `redis-outage-drill.json` — Stage-4 capacity summaries.

## Goal acceptance — what is PROVEN on real infrastructure

| Goal / Independent-Test element | Proven? | Where |
|---|---|---|
| API/worker kill + RabbitMQ redelivery → **exactly-once** (重复投递不重复副作用) | ✅ | `test_consumer_live.py::test_kill_mid_flight_redelivers_and_stays_exactly_once` — worker `taskkill /F` mid-flight, peer reaps + re-runs, one terminal `succeeded`, `attempts==2`, one `job.succeeded` event (real RabbitMQ) |
| Lease expiry → reclaim/reassign; restart recoverable | ✅ | `tests/recovery/` (real worker + SQLite/PG), `test_job_lease_pg_concurrency` (R27, real PG) |
| Cooperative cancellation mid-flight | ✅ | `test_consumer_live.py::test_live_cancel_mid_flight_finalizes_cancelled` |
| Queue saturation → **429/503 + Retry-After, no 5xx** (过载明确返回) | ✅ | `test_stage4_capacity.py::test_overload_burst_sheds_load_without_5xx` (100 concurrent, real Redis) |
| **Redis short outage** → fail closed 503, then recover | ✅ | `test_stage4_capacity.py::test_redis_outage_fails_closed_then_recovers` |
| Disconnected SSE resources freed (gauge→0, teardown timed) | ✅ | `test_stage4_capacity.py::test_concurrent_sse_replay_frees_resources` + `test_sse_endpoint` (unit) |
| Cross-instance quota atomicity | ✅ | `test_coordinator_admission.py` (real Redis Lua) |
| SSE resume / Redis-trim → PG durable snapshot | ✅ | `test_sse_resume` / `test_run_snapshot_pg` (R27) |
| Retry backoff jitter (no synchronized retry storm) | ✅ | `test_retry_jitter.py` |

## What is NOT covered — the honest remaining gap (why Checkpoint is withheld)

**1000 concurrent *held-open* SSE connections** (the Independent Test's headline
scenario) is **not** achieved, for two honest reasons:

1. **Live-tail producer not wired.** `/api/v2/runs/{run_id}/events` is a
   *replay-then-close* endpoint: it passes `channel=None`. The live tail that would
   hold a connection open — fanning worker-produced events into each connection's
   `BoundedEventChannel` — is implemented at the generator level but not wired to a
   producer. It was gated on the Celery consumer (T064); T064 now exists, so this is
   *unblocked* but still **unbuilt** (needs a worker→connection fan-out, e.g. Redis
   pub/sub, plus the endpoint holding open until run-terminal/disconnect).
2. **Per-connection DB pool pinning.** Each in-flight SSE pins a DB connection (via
   the principal dependency) for the request lifetime, so concurrent SSE is bounded
   by `DATABASE_POOL_SIZE`/`MAX_OVERFLOW`. Concurrent SSE *replay* cleanup is proven
   at N=10 (reliably within the pool); a 1000-held-open run needs the live tail +
   a tuned PG pool.

The legacy **Locust** `sse`/`saturation` profiles target the v1 `/api/chat` surface
(need a live LLM, not the Stage-4 run/SSE/quota surface) and were **not** run; they
are superseded for this phase by the in-process `test_stage4_capacity.py`, which
tests the correct surface without faking an LLM.

## Checkpoint assessment

**NOT declared.** Nearly every Phase-4 invariant — exactly-once redelivery under a
real `kill -9`, lease reclaim, restart recovery, 429/503 + Retry-After overload
shedding, Redis-outage fail-closed, SSE resource cleanup, cross-instance quota
atomicity — is proven on real infrastructure. The one unmet acceptance scenario is
**1000 concurrent held-open SSE connections**, which requires building the live-tail
fan-out producer (now unblocked by T064) and running it against a tuned PG pool.
Until that exists and passes, the Checkpoint is honestly withheld.
