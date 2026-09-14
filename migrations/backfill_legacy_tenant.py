"""Restartable, batched backfill of tenant ownership for pre-tenant rows.

Migration-control module for stage 2 (task T031).

What it does
------------
``001_enterprise_expand`` is purely additive: legacy tables keep their data while
``tenant_id`` exists as a NULLABLE column, and a ``legacy`` tenant is seeded so
that every pre-tenant row has a real owner to point at. This module fills that
column in bounded batches, deriving ownership from the parent row whenever the
row has one (a message belongs to its conversation's tenant, a document to its
knowledge base's tenant, and so on) and falling back to the ``legacy`` tenant
only when no parent resolves. The fallback is counted separately per table so an
operator can see how much ownership had to be guessed instead of derived.

The matching ``002_enterprise_enforce`` revision refuses to run until this
backfill has reconciled, so the nullable window can never silently become a
production state.

State
-----
The script creates and maintains ``public.migration_backfill_state``. That table
is **migration-control state, not application schema**: it records the progress
of this one-off backfill (cursor, source/target counts, checksum, failures) and
is owned by the migration tooling. Application code must not read or write it,
and it is deliberately not part of ``backend/app/db/models.py``.

The table is created with ``CREATE TABLE IF NOT EXISTS`` because the script has
to be runnable against a database where a previous run already created it, and
because a failed run must leave readable evidence behind.

Restartability and idempotency
------------------------------
* Rows are processed in ``id`` order with a keyset cursor (``id > cursor``), so a
  batch is bounded by work rather than by scan position and an interrupted run
  resumes from the last persisted cursor.
* Every batch persists its cursor and counts in the same transaction as the
  update that consumed it, so a crash either loses the whole batch (safe to
  redo) or keeps it (cursor already advanced).
* A row is only ever written when ``tenant_id IS NULL``, so re-running the whole
  backfill changes no counts. The checksum is recomputed from the final identity
  set of every table after the last table finishes, which makes it a pure
  function of the backfilled rows: a second full run reproduces it exactly.
* Rows whose tenant cannot be derived and whose fallback fails are recorded in a
  bounded ``failures`` list (reasons only, never row payload) and counted; the
  table is then left incomplete so the enforce phase refuses to proceed.

CLI
---
    python -m migrations.backfill_legacy_tenant --batch-size 500
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy import Connection, Engine, text

# Stable identifier of the tenant created and seeded by ``001_enterprise_expand``.
LEGACY_TENANT_ID: Final = "00000000-0000-0000-0000-000000000001"
LEGACY_TENANT_CODE: Final = "legacy"

LEDGER_TABLE: Final = "migration_backfill_state"

# Statuses persisted per table.
STATUS_PENDING: Final = "pending"
STATUS_RUNNING: Final = "running"
STATUS_COMPLETED: Final = "completed"
STATUS_FAILED: Final = "failed"

DEFAULT_BATCH_SIZE: Final = 500
MAX_RECORDED_FAILURES: Final = 100


@dataclass(frozen=True)
class TenantDerivation:
    """How a table's tenant owner is resolved.

    ``parent_table``/``parent_fk``/``parent_key`` describe the optional link used
    to inherit ownership. ``tenant_column`` is the column on the parent whose
    ``tenant_id`` is inherited; when it is ``None`` the link itself only proves
    the parent relationship (used for tables that carry no tenant column yet).
    """

    parent_table: str | None = None
    parent_fk: str | None = None
    parent_key: str | None = None
    tenant_column: str | None = None


@dataclass(frozen=True)
class BackfillTable:
    """One tenant-scoped table in backfill scope, with its derivation rule."""

    name: str
    derivation: TenantDerivation
    # ``True`` when ``001`` already created the nullable ``tenant_id`` column.
    tenant_column_exists: bool


def _parent(table: str, key: str, tenant_column: str | None = "tenant_id") -> TenantDerivation:
    return TenantDerivation(
        parent_table=table,
        parent_fk=key,
        parent_key="id",
        tenant_column=tenant_column,
    )


# Ordered so every parent is finished before the tables that inherit from it.
BACKFILL_TABLES: Final[tuple[BackfillTable, ...]] = (
    # Identities are the remaining nullable-ownership tables. ``001`` claims the
    # users that existed when it ran, so anything still unowned afterwards is
    # legacy data that the ``legacy`` tenant owns; without this the
    # reconciliation check would report unowned users forever.
    BackfillTable("users", TenantDerivation(), True),
    BackfillTable("roles", TenantDerivation(), True),
    # Root legacy rows: no parent carries ownership, so they fall back to legacy.
    BackfillTable("conversations", TenantDerivation(), True),
    BackfillTable("knowledge_bases", TenantDerivation(), True),
    BackfillTable("eval_runs", TenantDerivation(), True),
    # ``eval_cases`` sits in the same position as its eval siblings: the expand
    # migration never gave it a tenant column, so the additive step below owns
    # adding it. Declaring it as already present left the table without
    # ``tenant_id`` while the batch statement referenced it, which failed the
    # whole run.
    BackfillTable("eval_cases", TenantDerivation(), False),
    # Children inherit the parent's tenant instead of being guessed.
    BackfillTable("messages", _parent("conversations", "conversation_id"), True),
    BackfillTable("memory_items", TenantDerivation(), True),
    BackfillTable("knowledge_documents", _parent("knowledge_bases", "knowledge_base_id"), True),
    BackfillTable("drafts", TenantDerivation(), True),
    BackfillTable("ai_query_logs", _parent("conversations", "conversation_id"), False),
    BackfillTable("eval_results", _parent("eval_runs", "eval_run_id"), False),
    BackfillTable("retrieval_eval_items", _parent("eval_cases", "eval_case_id"), False),
)

TABLE_NAMES: Final[tuple[str, ...]] = tuple(table.name for table in BACKFILL_TABLES)

# Tables whose ``tenant_id`` column is created here because the expand migration
# predates them. They still belong to the tenant-ownership set, so the backfill
# has to own the additive step that makes them addressable.
TENANT_COLUMN_ADDED_BY_BACKFILL: Final[tuple[BackfillTable, ...]] = tuple(
    table for table in BACKFILL_TABLES if not table.tenant_column_exists
)

FAILURE_PARENT_UNRESOLVED: Final = "parent_row_missing"
FAILURE_FALLBACK_TENANT_MISSING: Final = "legacy_tenant_missing"


@dataclass
class TableState:
    """Persisted state of one table's backfill."""

    table_name: str
    status: str = STATUS_PENDING
    cursor_value: str | None = None
    source_count: int = 0
    target_count: int = 0
    legacy_fallback_count: int = 0
    checksum: str | None = None
    failure_count: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None

    @property
    def complete(self) -> bool:
        """Return True when the table needs no further backfill work."""
        return self.status == STATUS_COMPLETED


@dataclass
class BackfillResult:
    """Outcome of one backfill invocation, for CLI reporting and tests."""

    tables: dict[str, TableState] = field(default_factory=dict)
    batches: int = 0
    stopped_early: bool = False

    @property
    def complete(self) -> bool:
        """Return True when every table reconciled without recorded failures."""
        return all(state.complete for state in self.tables.values()) and bool(self.tables)

    @property
    def checksum(self) -> str | None:
        """Aggregate checksum over the per-table checksums, when all are known."""
        per_table = {
            name: state.checksum for name, state in sorted(self.tables.items())
        }
        if any(value is None for value in per_table.values()):
            return None
        return _aggregate_checksum(per_table)

    def counts(self) -> dict[str, tuple[int, int]]:
        """Return ``{table: (source_count, target_count)}`` for reconciliation."""
        return {
            name: (state.source_count, state.target_count)
            for name, state in self.tables.items()
        }


def utc_now() -> datetime:
    """Return the current UTC timestamp without tzinfo, matching column storage."""
    return datetime.now(UTC).replace(tzinfo=None)


# --- Ledger -----------------------------------------------------------------


CREATE_LEDGER_SQL: Final = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    table_name varchar(63) PRIMARY KEY,
    status varchar(20) NOT NULL DEFAULT 'pending',
    cursor_value varchar(64),
    source_count integer NOT NULL DEFAULT 0,
    target_count integer NOT NULL DEFAULT 0,
    legacy_fallback_count integer NOT NULL DEFAULT 0,
    checksum varchar(64),
    failure_count integer NOT NULL DEFAULT 0,
    failures jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at timestamp,
    finished_at timestamp,
    updated_at timestamp NOT NULL DEFAULT now()
)
"""


def ensure_ledger(connection: Connection) -> None:
    """Create the migration-control ledger when it does not exist yet.

    ``CREATE TABLE IF NOT EXISTS`` keeps the script runnable on a database where
    an earlier run already created the ledger, which is what makes an
    interrupted run resumable instead of fatal.
    """
    connection.execute(text(CREATE_LEDGER_SQL))


def read_ledger(connection: Connection) -> dict[str, TableState]:
    """Read every persisted table state keyed by table name."""
    rows = connection.execute(
        text(
            f"""
            SELECT table_name, status, cursor_value, source_count, target_count,
                   legacy_fallback_count, checksum, failure_count, failures,
                   started_at, finished_at, updated_at
            FROM {LEDGER_TABLE}
            """
        )
    ).mappings()
    states: dict[str, TableState] = {}
    for row in rows:
        states[str(row["table_name"])] = TableState(
            table_name=str(row["table_name"]),
            status=str(row["status"]),
            cursor_value=None if row["cursor_value"] is None else str(row["cursor_value"]),
            source_count=int(row["source_count"]),
            target_count=int(row["target_count"]),
            legacy_fallback_count=int(row["legacy_fallback_count"]),
            checksum=None if row["checksum"] is None else str(row["checksum"]),
            failure_count=int(row["failure_count"]),
            failures=list(row["failures"] or []),
            started_at=_iso(row["started_at"]),
            finished_at=_iso(row["finished_at"]),
            updated_at=_iso(row["updated_at"]),
        )
    return states


def reconciliation_report(connection: Connection) -> list[dict[str, Any]]:
    """Return the persisted cursor/count/checksum/failure evidence per table.

    This is the readable reconciliation surface: enforcement and operators use it
    to decide whether the backfill is trustworthy, rather than re-deriving it.
    """
    return [
        {
            "table_name": state.table_name,
            "status": state.status,
            "cursor_value": state.cursor_value,
            "source_count": state.source_count,
            "target_count": state.target_count,
            "legacy_fallback_count": state.legacy_fallback_count,
            "checksum": state.checksum,
            "failure_count": state.failure_count,
            "failures": state.failures,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "updated_at": state.updated_at,
        }
        for _, state in sorted(read_ledger(connection).items())
    ]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat()
    return str(value)


def _persist_state(connection: Connection, state: TableState) -> None:
    """Upsert one table's state; runs in the same transaction as its batch."""
    state.updated_at = utc_now().isoformat()
    connection.execute(
        text(
            f"""
            INSERT INTO {LEDGER_TABLE} (
                table_name, status, cursor_value, source_count, target_count,
                legacy_fallback_count, checksum, failure_count, failures,
                started_at, finished_at, updated_at
            ) VALUES (
                :table_name, :status, :cursor_value, :source_count, :target_count,
                :legacy_fallback_count, :checksum, :failure_count,
                CAST(:failures AS jsonb), CAST(:started_at AS timestamp),
                CAST(:finished_at AS timestamp), CAST(:updated_at AS timestamp)
            )
            ON CONFLICT (table_name) DO UPDATE SET
                status = EXCLUDED.status,
                cursor_value = EXCLUDED.cursor_value,
                source_count = EXCLUDED.source_count,
                target_count = EXCLUDED.target_count,
                legacy_fallback_count = EXCLUDED.legacy_fallback_count,
                checksum = EXCLUDED.checksum,
                failure_count = EXCLUDED.failure_count,
                failures = EXCLUDED.failures,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "table_name": state.table_name,
            "status": state.status,
            "cursor_value": state.cursor_value,
            "source_count": state.source_count,
            "target_count": state.target_count,
            "legacy_fallback_count": state.legacy_fallback_count,
            "checksum": state.checksum,
            "failure_count": state.failure_count,
            "failures": json.dumps(state.failures),
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "updated_at": state.updated_at,
        },
    )


def record_failure(state: TableState, *, row_id: str | None, reason: str) -> None:
    """Record one bounded failure reason without copying row payload."""
    state.failure_count += 1
    if len(state.failures) < MAX_RECORDED_FAILURES:
        state.failures.append({"row_id": row_id, "reason": reason})


# --- Column preparation -----------------------------------------------------


def tenant_column_present(connection: Connection, table: str) -> bool:
    """Return True when ``table.tenant_id`` already exists."""
    return (
        connection.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table
                  AND column_name = 'tenant_id'
                """
            ),
            {"table": table},
        ).first()
        is not None
    )


def ensure_tenant_columns(connection: Connection) -> list[str]:
    """Add the nullable ``tenant_id`` column to tables whose expand predates it.

    ``001_enterprise_expand`` created the nullable window for the original legacy
    tables. The eval and query-log tables are in the backfill scope but were not
    in that set, so the additive half of their ownership column is idempotently
    supplied here, inside the same migration-control step that fills it. The
    column stays NULLABLE: only ``002_enterprise_enforce`` may tighten it.
    """
    added: list[str] = []
    for table in TENANT_COLUMN_ADDED_BY_BACKFILL:
        if tenant_column_present(connection, table.name):
            continue
        connection.execute(
            text(
                f"ALTER TABLE {table.name} "
                "ADD COLUMN tenant_id varchar(36) REFERENCES tenants(id)"
            )
        )
        added.append(table.name)
    return added


# --- Checksums --------------------------------------------------------------


def table_checksum(connection: Connection, table: str) -> str:
    """Hash the ordered identity set of ``table`` after backfill.

    The digest is over the row identifiers only, ordered by ``id``, so it is a
    deterministic function of the backfilled identity set: two runs that reached
    the same rows produce the same value, and an out-of-band insert or delete
    changes it.

    A server-side cursor keeps the scan from materialising the whole identity
    set. The option MUST be cleared again: SQLAlchemy mutates the ``Connection``
    in place, and a connection left in streaming mode renders the *next*
    statement as ``DECLARE ... CURSOR FOR INSERT ...``, which PostgreSQL rejects
    as a syntax error at ``INSERT``.
    """
    digest = hashlib.sha256()
    try:
        result = connection.execution_options(stream_results=True).execute(
            text(f"SELECT id FROM {table} ORDER BY id")
        )
        for (row_id,) in result:
            digest.update(str(row_id).encode("utf-8"))
            digest.update(b"\n")
    finally:
        connection.execution_options(stream_results=False)
    return digest.hexdigest()


def _aggregate_checksum(per_table: dict[str, str | None]) -> str:
    digest = hashlib.sha256()
    for name, value in per_table.items():
        digest.update(f"{name}:{value}\n".encode())
    return digest.hexdigest()


# --- Batch execution --------------------------------------------------------


def _pending_batch_sql(table: BackfillTable) -> str:
    """Build the keyset batch statement for one table.

    One statement gathers the next ``:batch_size`` rows by ``id``, updates the
    ones whose parent resolves, and reports how many rows were covered, matched
    and inherited versus resolved without a parent.

    Root tables (a conversation, a knowledge base, an eval run) have no parent to
    inherit from, so they select no join at all and every covered row is
    attributed to the ``legacy`` tenant. Emitting the join unconditionally would
    render a literal ``LEFT JOIN None``.

    The batch CTE must carry the parent foreign key alongside ``id``: the join
    is evaluated against the CTE, so a batch projecting only ``id`` cannot
    reference the linking column at all.
    """
    derivation = table.derivation
    if derivation.parent_table is None:
        batch_columns = "id"
        parent_join = ""
        resolved_tenant = ":legacy_tenant_id"
        inherited_tenant = "NULL"
    else:
        batch_columns = f"id, {derivation.parent_fk}"
        parent_join = (
            f"LEFT JOIN {derivation.parent_table} AS p "
            f"ON p.{derivation.parent_key} = b.{derivation.parent_fk}"
        )
        resolved_tenant = f"p.{derivation.tenant_column}"
        inherited_tenant = f"p.{derivation.tenant_column}"
    return f"""
        WITH batch AS (
            SELECT {batch_columns} FROM {table.name}
            WHERE id > COALESCE(CAST(:cursor AS varchar), '')
            ORDER BY id
            LIMIT :batch_size
        ),
        resolved AS (
            SELECT b.id AS id,
                   COALESCE({resolved_tenant}, :legacy_tenant_id) AS resolved_tenant_id,
                   {inherited_tenant} AS inherited_tenant_id
            FROM batch AS b
            {parent_join}
        ),
        updated AS (
            UPDATE {table.name} AS t
               SET tenant_id = r.resolved_tenant_id
              FROM resolved AS r
             WHERE t.id = r.id AND t.tenant_id IS NULL
            RETURNING t.id
        )
        SELECT
            (SELECT count(*) FROM batch) AS source_count,
            (SELECT count(*) FROM updated) AS target_count,
            (SELECT count(*) FROM resolved WHERE inherited_tenant_id IS NULL) AS legacy_count,
            (SELECT COALESCE(max(id), '') FROM batch) AS cursor_value,
            (
                SELECT COALESCE(
                    json_agg(json_build_object('id', r.id, 'reason', CAST(:reason AS text))),
                    '[]'::json
                )
                FROM resolved AS r
                WHERE r.id NOT IN (SELECT id FROM updated)
            ) AS unresolved
    """

def _fallback_batch_sql(table: BackfillTable) -> str:
    """Backfill rows that have no parent link at all onto the ``legacy`` tenant.

    Rows that cannot name a parent (a draft with no conversation, a retrieval
    item with no case) have no owner to inherit from, so the seeded ``legacy``
    tenant owns them and the count is recorded separately.
    """
    derivation = table.derivation
    link_filter = (
        f"AND {derivation.parent_fk} IS NULL" if derivation.parent_table is not None else ""
    )
    return f"""
        WITH batch AS (
            SELECT id FROM {table.name}
            WHERE id > COALESCE(CAST(:cursor AS varchar), '')
              AND tenant_id IS NULL
              {link_filter}
            ORDER BY id
            LIMIT :batch_size
        ),
        updated AS (
            UPDATE {table.name} AS t
               SET tenant_id = :legacy_tenant_id
              FROM batch AS b
             WHERE t.id = b.id AND t.tenant_id IS NULL
            RETURNING t.id
        )
        SELECT
            (SELECT count(*) FROM batch) AS source_count,
            (SELECT count(*) FROM updated) AS target_count,
            (SELECT COALESCE(max(id), '') FROM batch) AS cursor_value
    """


@dataclass(frozen=True)
class BatchOutcome:
    """Result of one batch: how much moved and whether the table settled."""

    updated: int
    settled: bool


def backfill_table_batch(
    connection: Connection,
    table: BackfillTable,
    state: TableState,
    *,
    batch_size: int,
    legacy_tenant_id: str = LEGACY_TENANT_ID,
) -> BatchOutcome:
    """Process at most one batch and persist the resulting state.

    The cursor always advances to the highest ``id`` the batch *covered*, not the
    highest one it could attribute, so a row whose tenant cannot be derived can
    never stall the scan. Attribution itself is resolved in two passes: rows with
    a parent link inherit the parent's tenant, and rows with no link (or a link
    that does not resolve) fall back to the ``legacy`` tenant in a second pass
    that is counted separately.

    ``settled`` is True only when nothing is left to process *and* every row has
    an owner; a table that ends with unresolved rows keeps ``status='failed'``
    and no checksum, so enforce still refuses.
    """
    row = connection.execute(
        text(_pending_batch_sql(table)),
        {
            "cursor": state.cursor_value,
            "batch_size": batch_size,
            "legacy_tenant_id": legacy_tenant_id,
            "reason": FAILURE_PARENT_UNRESOLVED,
        },
    ).mappings().one()

    source_count = int(row["source_count"])
    target_count = int(row["target_count"])
    legacy_count = int(row["legacy_count"])
    cursor_value = str(row["cursor_value"] or "")
    unresolved = list(row["unresolved"] or [])

    if source_count:
        state.source_count += source_count
        state.target_count += target_count
        state.legacy_fallback_count += legacy_count
        if cursor_value:
            state.cursor_value = cursor_value
        state.status = STATUS_RUNNING
        # Cursor and counts are persisted in the same transaction as the update
        # that produced them, so an interrupted run resumes without re-applying.
        _persist_state(connection, state)
        return BatchOutcome(updated=target_count, settled=False)

    if not unresolved:
        # Nothing pending and nothing left unattributed: the table is settled.
        state.status = STATUS_COMPLETED
        state.finished_at = utc_now().isoformat()
        _persist_state(connection, state)
        return BatchOutcome(updated=0, settled=True)

    # Rows whose parent link did not resolve have no owner to inherit, so the
    # seeded legacy tenant owns them and the fallback is counted separately.
    fallback_row = connection.execute(
        text(_fallback_batch_sql(table)),
        {
            "cursor": state.cursor_value,
            "batch_size": batch_size,
            "legacy_tenant_id": legacy_tenant_id,
        },
    ).mappings().one()
    fallback_source = int(fallback_row["source_count"])
    fallback_updated = int(fallback_row["target_count"])
    fallback_cursor = str(fallback_row["cursor_value"] or "")
    if fallback_source:
        state.source_count += fallback_source
        state.target_count += fallback_updated
        state.legacy_fallback_count += fallback_updated
        if fallback_cursor:
            state.cursor_value = fallback_cursor
        state.status = STATUS_RUNNING
        _persist_state(connection, state)
        return BatchOutcome(updated=fallback_updated, settled=False)

    # Neither pass could attribute these rows; record the bounded reasons and
    # leave the table failed so the enforce guard keeps refusing.
    for entry in unresolved:
        record_failure(state, row_id=str(entry.get("id")), reason=FAILURE_PARENT_UNRESOLVED)
    state.status = STATUS_FAILED
    _persist_state(connection, state)
    return BatchOutcome(updated=0, settled=True)


StopCheck = Callable[[str, int], bool]

# Hard ceiling on batches per table so a pathological table can never turn a
# migration into an unbounded loop; a run that hits it fails loudly instead.
MAX_BATCHES_PER_TABLE: Final = 1_000_000


def run_backfill(
    connection: Connection,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    legacy_tenant_id: str = LEGACY_TENANT_ID,
    should_stop: StopCheck | None = None,
    tables: Sequence[str] | None = None,
) -> BackfillResult:
    """Run the restartable backfill to completion on ``connection``.

    ``should_stop`` is consulted after every batch with ``(table_name,
    batches_done)``; returning True stops the run at that batch boundary so the
    caller can prove the next invocation resumes from the persisted cursor.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    ensure_ledger(connection)
    ensure_tenant_columns(connection)

    selected = tuple(tables) if tables is not None else TABLE_NAMES
    unknown = sorted(set(selected) - set(TABLE_NAMES))
    if unknown:
        raise ValueError(f"Unknown backfill tables: {', '.join(unknown)}")

    states = read_ledger(connection)
    result = BackfillResult(tables={})
    for table in BACKFILL_TABLES:
        if table.name not in selected:
            continue
        state = states.get(table.name, TableState(table_name=table.name))
        result.tables[table.name] = state
        if state.complete:
            # Completed tables are skipped, which is what makes a second full
            # run cheap and free of side effects.
            continue
        if state.started_at is None:
            state.started_at = utc_now().isoformat()
        _persist_state(connection, state)
        table_batches = 0
        while True:
            outcome = backfill_table_batch(
                connection,
                table,
                state,
                batch_size=batch_size,
                legacy_tenant_id=legacy_tenant_id,
            )
            result.batches += 1
            table_batches += 1
            if should_stop is not None and should_stop(table.name, result.batches):
                result.stopped_early = True
                return result
            if outcome.settled:
                break
            if table_batches >= MAX_BATCHES_PER_TABLE:
                raise RuntimeError(
                    f"{table.name} did not settle after {table_batches} batches; "
                    "the backfill is not making progress"
                )

    if not result.stopped_early:
        # Checksums are computed only once every table is at its final state, so
        # they are a pure function of the backfilled identity set.
        for table in BACKFILL_TABLES:
            if table.name not in result.tables:
                continue
            state = result.tables[table.name]
            if state.status != STATUS_COMPLETED:
                continue
            state.checksum = table_checksum(connection, table.name)
            _persist_state(connection, state)
    return result


def verify_backfill(connection: Connection, result: BackfillResult) -> None:
    """Raise when the run is not a complete, failure-free reconciliation.

    Missing legacy tenant, unresolved parent rows and recorded failures all leave
    the ownership column partially NULL; surfacing that here keeps a partial
    backfill from looking like a successful one.
    """
    problems: list[str] = []
    for name, state in sorted(result.tables.items()):
        if not state.complete:
            problems.append(
                f"{name}: status={state.status} failures={state.failure_count} "
                f"cursor={state.cursor_value!r}"
            )
    null_rows = connection.execute(
        text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
            "ORDER BY table_name"
        )
    ).scalars()
    remaining: list[str] = []
    for table in null_rows:
        count = connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
        ).scalar_one()
        if count:
            remaining.append(f"{table}={count}")
    if remaining:
        problems.append("rows still without a tenant: " + ", ".join(remaining))
    if problems:
        raise RuntimeError("tenant backfill did not reconcile: " + "; ".join(problems))


def run_backfill_on_url(url: str, *, batch_size: int = DEFAULT_BATCH_SIZE) -> BackfillResult:
    """Convenience entry point that owns the engine it creates."""
    engine = create_engine_for(url)
    try:
        with engine.begin() as connection:
            return run_backfill(connection, batch_size=batch_size)
    finally:
        engine.dispose()


def create_engine_for(url: str) -> Engine:
    """Create a synchronous engine for a migration-control connection."""
    return sa.create_engine(url, future=True)


def _env_url() -> str:
    from backend.app.core.config import get_settings

    return os.environ.get("DATABASE_URL") or get_settings().DATABASE_URL


def _format_report(result: BackfillResult) -> str:
    lines = ["table                 status      source   target   fallback  checksum"]
    for name in sorted(result.tables):
        state = result.tables[name]
        checksum = (state.checksum or "-")[:12]
        lines.append(
            f"{name:<21} {state.status:<11} {state.source_count:>6} "
            f"{state.target_count:>8} {state.legacy_fallback_count:>9}  {checksum}"
        )
    aggregate = result.checksum
    lines.append(f"batches={result.batches} stopped_early={result.stopped_early}")
    lines.append(f"aggregate_checksum={aggregate}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for ``python -m migrations.backfill_legacy_tenant``."""
    parser = argparse.ArgumentParser(
        prog="python -m migrations.backfill_legacy_tenant",
        description=(
            "Restartable batched backfill of tenant ownership for pre-tenant rows. "
            "Writes migration-control state to migration_backfill_state."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows processed per batch (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="SQLAlchemy URL to migrate; defaults to DATABASE_URL",
    )
    parser.add_argument(
        "--stop-after-batches",
        type=int,
        default=None,
        help="stop cleanly after N batches (used to rehearse an interrupted run)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print the persisted reconciliation evidence without backfilling",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    args = build_parser().parse_args(argv)
    url = args.url or _env_url()
    engine = create_engine_for(url)
    try:
        if args.report_only:
            with engine.connect() as connection:
                ensure_ledger(connection)
                report = reconciliation_report(connection)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0

        stop_after = args.stop_after_batches

        def should_stop(_table: str, batches: int) -> bool:
            return stop_after is not None and batches >= stop_after

        with engine.begin() as connection:
            result = run_backfill(
                connection,
                batch_size=args.batch_size,
                should_stop=should_stop,
            )
            if not result.stopped_early:
                verify_backfill(connection, result)
        print(_format_report(result))
        if result.complete:
            return 0
        if result.stopped_early:
            print("stopped early; re-run to resume from the persisted cursor")
            return 0
        print("backfill incomplete; see migration_backfill_state", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def iter_reconciliation(connection: Connection) -> Iterator[dict[str, Any]]:
    """Iterate persisted reconciliation rows for callers that stream evidence."""
    yield from reconciliation_report(connection)


def null_tenant_counts(connection: Connection, tables: Iterable[str]) -> dict[str, int]:
    """Return how many rows in each table still have no tenant owner."""
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
            ).scalar_one()
        )
    return counts


if __name__ == "__main__":  # pragma: no cover - CLI wiring
    raise SystemExit(main())
