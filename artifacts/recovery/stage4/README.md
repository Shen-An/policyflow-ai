# Stage-4 Recovery Evidence (broker-free + PG-backed subset)

**Captured:** 2026-10-01 (UTC) · branch `master` · see `pytest-pg-stage4.txt`, `infra-probe.txt`.

This directory holds **honest, partial** evidence for Phase 4 / User Story 2
「大规模并发下稳定使用」(tasks T055–T072). It is deliberately **not** a Checkpoint
declaration. It records exactly what was exercised against **real infrastructure
that is up on this machine**, and names precisely what is **still gated** on
infrastructure that is **not** up here.

## Infrastructure reality at capture time (see `infra-probe.txt`)

| Component  | State on this machine | Used by this evidence? |
|------------|-----------------------|------------------------|
| PostgreSQL | **UP** — 17.10 on `127.0.0.1:55432`, DBs `policyflow` + `policyflow_test`, role `policyflow` | **Yes** — every suite below ran against it |
| Redis      | **UP** — `127.0.0.1:6379` (`PING` → `+PONG`) | **Yes** — quota coordinator / admission paths |
| RabbitMQ   | **DOWN** — `5672`/`15672` both time out (no erlang, no broker binary, docker daemon not running) | **No** — this is the honest gate |

## What this evidence DOES cover (137 tests, all green on live PG + Redis)

Ran with `POLICYFLOW_TEST_DATABASE_URL=postgresql+psycopg://policyflow:***@127.0.0.1:55432/policyflow_test`.

- **SUITE 1 — integration, PG-authoritative (28 passed)**: durable-job lease
  concurrency under real PG row locks, quota ledger on PG, run-snapshot
  persistence, Stage-4 migration against a real PG schema, multi-instance
  behaviour, document-index claim. These prove the authoritative state machine
  on the **production-shaped** database, not SQLite.
- **SUITE 2 — contract (99 passed)**: job state machine, outbox publisher &
  dedupe, `/api/runs` API, SSE resume / endpoint / cleanup, quota admission &
  coordinator admission (429/503 + `Retry-After`), job-state observable gauge,
  LLM concurrency/token telemetry call-site (R26), durable-job runner, Celery
  config shape, Stage-4 telemetry, document-index & eval-run durable submission.
- **SUITE 3 — recovery (10 passed)**: redelivery **idempotency** (same payload
  re-enqueue is a no-op; completion does not re-transition; redelivery after a
  terminal state does not reopen; cancel-finalize is a no-op; duplicate outbox
  publication is rejected **at the database**) and **restart recovery** (dead
  worker's leased job reclaimed/reassigned; running job reaped on restart; reap
  at attempt-budget is terminal; succeeded job untouched; sweep is idempotent).

These directly exercise the goal's core invariants — *重复投递不重复副作用* and
*重启可恢复* — at the DB / state-machine layer that the broker would drive.

## What this evidence does NOT cover (genuinely gated on RabbitMQ — stays `[~]`/`[ ]`)

The recovery suites above **simulate** redelivery and worker death by driving the
state machine directly; they do **not** run a live broker consumer. The following
require a real RabbitMQ (quorum queues) + Celery worker and are **not** claimed
green here:

- **T062 (live transport) / T064 (consumers + worker-side lease release) /
  T065 (real broker transport)** — need a live broker + worker loop.
- **T072 full** — Locust 1000-SSE / queue-saturation 429/503 load profile, and
  live-broker redelivery under `kill -9`. Only the **broker-free + PG subset** of
  T072's evidence is produced here.
- **Independent Test** — API/worker kill, RabbitMQ redelivery, lease expiry under
  a live broker, Redis short-outage drill, 1000 concurrent SSE. Not run.

RabbitMQ is **not mocked** to manufacture a pass. It is honestly absent, so the
broker-dependent tasks remain `[~]`/`[ ]` in `tasks.md` and the Checkpoint is
**not** declared.

## Reproduce

```bash
export POLICYFLOW_TEST_DATABASE_URL="postgresql+psycopg://policyflow:<pw>@127.0.0.1:55432/policyflow_test"
python -m pytest tests/integration/test_job_lease_pg_concurrency.py \
  tests/integration/test_quota_ledger_pg.py tests/integration/test_run_snapshot_pg.py \
  tests/integration/test_stage4_migration.py tests/integration/test_multi_instance.py \
  tests/integration/test_index_claim.py tests/contract/ tests/recovery/ -q
```

(`policyflow` role is `trust`-authed for localhost in this `.pgdata`, so any
password string connects; the real value is never recorded here.)
