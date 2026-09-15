"""Tenant-scoped async repositories and the unit of work that owns a transaction.

This module is the only sanctioned way for application code to read or write the
PostgreSQL business authority. It turns the modelling rules of
``specs/001-enterprise-agent-refactor/data-model.md`` and the
``AuthorizationService`` / ``Error Contract`` sections of
``specs/001-enterprise-agent-refactor/contracts/internal-contracts.md`` into code.

Rules enforced here, not merely documented:

1. **Explicit tenant.** Every protected operation takes ``tenant_id`` as its
   first parameter, with no default. An empty or whitespace-only tenant raises
   :class:`TenantScopeError` before any SQL is built. The tenant must come from
   the authenticated principal, never from a request body, query string or
   header: this layer has no way to *derive* a tenant, only to enforce one.
2. **Invisibility.** A row owned by another tenant and a row that does not exist
   produce the *same* typed error, built by one shared helper, so a caller
   cannot use the repository as an enumeration oracle.
3. **Compare-and-set.** Every update takes ``expected_version`` and runs
   ``WHERE id = :id AND tenant_id = :tenant AND version = :expected`` with
   ``version = version + 1``. A stale version raises
   :class:`VersionConflictError`; last-write-wins is not representable.
4. **One transaction per unit of work.** :class:`UnitOfWork` owns a single
   ``AsyncSession`` and never shares it between tasks.
5. **RLS is defence in depth.** :func:`set_tenant_context` publishes the tenant
   to the connection so the ``tenant_isolation`` policies can reject a query
   that forgot its predicate. It never replaces the application predicate in
   this module, and it is inert on a database whose role is a superuser or has
   ``BYPASSRLS``.

Errors carry only identifiers that the caller already supplied plus stable
codes; credentials, connection strings, host paths and raw payloads never appear
in an error message, in an error detail or in a log record produced here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import Session

from backend.app.auth.authorization import ResourceRef
from backend.app.auth.error_codes import RESOURCE_NOT_FOUND, TENANT_NOT_FOUND
from backend.app.core.config import get_settings
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    RUN_STATUSES,
    AgentRun,
    AuditEvent,
    GraphCheckpointBinding,
    IdempotencyRecord,
    MemoryItem,
    Role,
    RunEvent,
    Tenant,
    User,
    UserRoleGrant,
    new_id,
    utc_now,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AUDIT_OUTCOME_ALLOWED",
    "AUDIT_OUTCOME_DENIED",
    "AUDIT_OUTCOME_FAILED",
    "AUDIT_OUTCOME_SUCCEEDED",
    "IDEMPOTENCY_CONFLICT",
    "IDEMPOTENCY_STATE_COMPLETED",
    "IDEMPOTENCY_STATE_FAILED",
    "IDEMPOTENCY_STATE_IN_PROGRESS",
    "MISSING_TENANT_SCOPE",
    "RESOURCE_KIND_AGENT_RUN",
    "RESOURCE_KIND_AUDIT_EVENT",
    "RESOURCE_KIND_CHECKPOINT_BINDING",
    "RESOURCE_KIND_ROLE",
    "RESOURCE_KIND_TENANT",
    "RESOURCE_KIND_USER",
    "RESOURCE_KIND_USER_ROLE_GRANT",
    "RESOURCE_KINDS",
    "TENANT_CONTEXT_SETTING",
    "VERSION_CONFLICT",
    "AgentRunRepository",
    "AuditEventRepository",
    "ClaimOutcome",
    "GraphCheckpointBindingRepository",
    "IdempotencyClaim",
    "IdempotencyConflictError",
    "IdempotencyRepository",
    "RepositoryError",
    "ResourceCatalogLookup",
    "ResourceNotFoundError",
    "RoleRepository",
    "RunEventRepository",
    "RunEventSequenceConflictError",
    "RunEventSequenceError",
    "RunStateTransitionError",
    "SqlResourceCatalog",
    "TERMINAL_RUN_STATUSES",
    "TenantNotFoundError",
    "TenantRepository",
    "TenantScopeError",
    "UnknownResourceKindError",
    "UserRepository",
    "UserRoleGrantRepository",
    "VersionConflictError",
    "append_audit_event",
    "close_resource_catalog_engine",
    "new_idempotency_key",
    "require_tenant",
    "require_version",
    "set_tenant_context",
    "unit_of_work",
]

logger = get_logger(__name__)

# --- Stable codes from the contract's Error Contract ------------------------

VERSION_CONFLICT = "VERSION_CONFLICT"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
# Codes local to this boundary; ``RESOURCE_NOT_FOUND``/``TENANT_NOT_FOUND`` come
# from ``backend.app.auth.error_codes`` so every boundary shares one vocabulary.
MISSING_TENANT_SCOPE = "MISSING_TENANT_SCOPE"

# --- Constrained values mirrored from the models ----------------------------

IDEMPOTENCY_STATE_IN_PROGRESS = "in_progress"
IDEMPOTENCY_STATE_COMPLETED = "completed"
IDEMPOTENCY_STATE_FAILED = "failed"

AUDIT_OUTCOME_ALLOWED = "allowed"
AUDIT_OUTCOME_DENIED = "denied"
AUDIT_OUTCOME_SUCCEEDED = "succeeded"
AUDIT_OUTCOME_FAILED = "failed"

# The connection-local setting the ``tenant_isolation`` RLS policies read. It
# mirrors ``migrations/versions/001_enterprise_expand.py``.
TENANT_CONTEXT_SETTING = "policyflow.tenant_id"

# Resource kinds this layer can answer presence questions for. The values double
# as the ``ResourceRef.kind`` vocabulary used by ``AuthorizationService``.
RESOURCE_KIND_TENANT = "tenant"
RESOURCE_KIND_USER = "user"
RESOURCE_KIND_ROLE = "role"
RESOURCE_KIND_USER_ROLE_GRANT = "user_role_grant"
RESOURCE_KIND_AGENT_RUN = "agent_run"
RESOURCE_KIND_CHECKPOINT_BINDING = "graph_checkpoint_binding"
RESOURCE_KIND_AUDIT_EVENT = "audit_event"
RESOURCE_KINDS: frozenset[str] = frozenset(
    {
        RESOURCE_KIND_TENANT,
        RESOURCE_KIND_USER,
        RESOURCE_KIND_ROLE,
        RESOURCE_KIND_USER_ROLE_GRANT,
        RESOURCE_KIND_AGENT_RUN,
        RESOURCE_KIND_CHECKPOINT_BINDING,
        RESOURCE_KIND_AUDIT_EVENT,
    }
)

# Run states that can never be left by a normal update. Recovering a terminal run
# is an audited administrative action that creates a *new* run, so this
# repository refuses the transition instead of silently resurrecting a run.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"cancelled", "succeeded", "terminal_failed", "timed_out"}
)

# Identifier columns are opaque strings; the printable bound keeps a hostile
# value from being echoed back verbatim inside an error message.
_MAX_MESSAGE_IDENTIFIER = 64

# A row is returned for reads that only expose scalar columns. ``SQLModel`` rows
# and ``Row`` objects both answer attribute access for every selected column, and
# this alias keeps the public signatures concrete instead of leaking ``Row[Any]``.
_Row = Any


# --- Typed repository errors ------------------------------------------------


def _require_text(value: object, field_name: str) -> str:
    """Return ``value`` as a trimmed non-empty string or raise ``ValueError``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _printable(value: object) -> str:
    """Return a short, escape-free rendering of an identifier for messages.

    Only the caller's own identifier is ever rendered, but it is bounded and
    stripped of control characters so an error message can never be used to
    smuggle a payload into a log line.
    """
    if value is None:
        return "<none>"
    text_value = str(value)
    cleaned = "".join(char if char.isprintable() else "?" for char in text_value)
    if len(cleaned) > _MAX_MESSAGE_IDENTIFIER:
        return f"{cleaned[:_MAX_MESSAGE_IDENTIFIER]}..."
    return cleaned


def _not_found_message(resource_kind: str, resource_id: object) -> str:
    """Build the single not-found message shape used for every invisible row.

    A row owned by another tenant and a row that never existed must be reported
    identically: same class, code, status, message and details.
    """
    return f"{resource_kind} {_printable(resource_id)} was not found"


class RepositoryError(ApplicationError):
    """Base class for every repository failure this module raises.

    Carrying a stable ``code`` means callers and the API error handler can branch
    on the contract vocabulary instead of on exception classes.
    """


class TenantScopeError(RepositoryError):
    """Raised when an operation is attempted without an explicit tenant.

    Every protected read and write names the tenant it acts for. An empty or
    ``None`` tenant is a programming error, so it fails immediately instead of
    silently widening the query to "all tenants".
    """

    def __init__(self, operation: str, tenant_id: object) -> None:
        """Report which operation lost its tenant scope."""
        super().__init__(
            MISSING_TENANT_SCOPE,
            "An explicit tenant_id is required for this operation",
            400,
            {"operation": operation, "tenant_supplied": tenant_id is not None},
        )


class ResourceNotFoundError(RepositoryError):
    """Raised when a protected row is absent *or* owned by another tenant.

    The two cases are deliberately indistinguishable, otherwise the error itself
    becomes an enumeration oracle for another tenant's identifiers.

    Args:
        resource_kind: the kind of resource being addressed, for the message.
        resource_id: the identifier the caller supplied, echoed bounded.
        code: the contract code; overridden only by subclasses that have a more
            specific code for the same shape (see :class:`TenantNotFoundError`).
    """

    def __init__(
        self,
        resource_kind: str,
        resource_id: object,
        *,
        code: str = RESOURCE_NOT_FOUND,
    ) -> None:
        """Describe the missing resource without revealing its owner."""
        super().__init__(
            code,
            _not_found_message(resource_kind, resource_id),
            404,
            {"resource_kind": resource_kind, "resource_id": _printable(resource_id)},
        )


class TenantNotFoundError(ResourceNotFoundError):
    """Raised when a tenant row is absent or not visible to the given subject.

    Shape-identical to :class:`ResourceNotFoundError` — same message, status and
    details — and differs only in the contract code (``TENANT_NOT_FOUND``), so a
    caller cannot distinguish "this tenant does not exist" from "this tenant is
    not yours".
    """

    def __init__(self, resource_id: object) -> None:
        """Report a missing tenant in the shared not-found shape."""
        super().__init__(RESOURCE_KIND_TENANT, resource_id, code=TENANT_NOT_FOUND)



class VersionConflictError(RepositoryError):
    """Raised when a compare-and-set update loses to a concurrent writer.

    The row existed under the caller's tenant but its ``version`` no longer
    matched ``expected_version``, so the caller's read is stale and its intended
    change was refused rather than applied last-write-wins.
    """

    def __init__(self, resource_kind: str, resource_id: object, expected_version: int) -> None:
        """Report the refused compare-and-set without echoing row contents."""
        super().__init__(
            VERSION_CONFLICT,
            (
                f"{resource_kind} {_printable(resource_id)} was modified by another "
                f"writer; expected version {expected_version}"
            ),
            409,
            {
                "resource_kind": resource_kind,
                "resource_id": _printable(resource_id),
                "expected_version": expected_version,
            },
        )
        self.expected_version = expected_version


class IdempotencyConflictError(RepositoryError):
    """Raised when one idempotency key is reused with a different digest.

    A key identifies exactly one logical operation for its lifetime, so a second
    request body under the same key is a client bug or a replay attack and must
    never be executed as if it were a fresh request.
    """

    def __init__(self, operation: str, idempotency_key: str) -> None:
        """Report the conflicting key without revealing either digest."""
        super().__init__(
            IDEMPOTENCY_CONFLICT,
            (
                f"idempotency key {_printable(idempotency_key)} for operation "
                f"{_printable(operation)} is already bound to a different request"
            ),
            409,
            {"operation": operation, "idempotency_key": _printable(idempotency_key)},
        )


class UnknownResourceKindError(RepositoryError):
    """Raised when a resource catalogue lookup names an unsupported kind.

    Guessing at an unknown kind would let a typo answer ``False`` for a resource
    that actually exists, which is worse than a loud failure.
    """

    def __init__(self, resource_kind: object) -> None:
        """Report the unsupported kind and the supported vocabulary."""
        super().__init__(
            "UNKNOWN_RESOURCE_KIND",
            f"Unsupported resource kind {_printable(resource_kind)}",
            400,
            {"resource_kind": _printable(resource_kind), "supported": sorted(RESOURCE_KINDS)},
        )


class RunEventSequenceError(RepositoryError):
    """Raised when a run-event sequence cannot be appended safely."""


class RunEventSequenceConflictError(RunEventSequenceError):
    """Raised when ``(run_id, sequence)`` is already used by another event.

    Event streams are append-only and a client resumes by sequence, so a
    duplicate must fail loudly: silently renumbering or overwriting would make a
    resumed stream skip or repeat a milestone.
    """

    def __init__(self, run_id: str, sequence: int) -> None:
        """Report the competing sequence for this run."""
        super().__init__(
            "RUN_EVENT_SEQUENCE_CONFLICT",
            f"run {_printable(run_id)} already has an event at sequence {sequence}",
            409,
            {"run_id": _printable(run_id), "sequence": sequence},
        )
        self.sequence = sequence


class RunStateTransitionError(RepositoryError):
    """Raised when a run update would leave a terminal state.

    Terminal states are final (see the ``AgentRun`` state machine); recovery is a
    new run, never an in-place resurrection of a finished one.
    """


# --- Input validation -------------------------------------------------------


def require_tenant(tenant_id: object, operation: str) -> str:
    """Return the explicit tenant for an operation, refusing an empty one.

    This is the single choke point for rule 1: the tenant is never defaulted,
    never inferred from a row and never taken from request input.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise TenantScopeError(operation, tenant_id)
    return tenant_id.strip()


def require_version(expected_version: object, operation: str) -> int:
    """Return the compare-and-set version, refusing a non-positive one.

    ``version`` starts at 1 in every mutable model, so ``0`` or a negative value
    can only be a caller mistake; accepting it would silently turn the update
    into a no-op instead of a conflict.
    """
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise ValueError(f"{operation}: expected_version must be an integer")
    if expected_version < 1:
        raise ValueError(f"{operation}: expected_version must be >= 1")
    return expected_version


def new_idempotency_key() -> str:
    """Return a fresh idempotency key for a caller that has none.

    Server-generated keys are useful for internally retried work; client and
    connector calls always supply their own key so a retry joins the original
    operation.
    """
    return str(uuid4())


# --- Shared SQLAlchemy helpers ---------------------------------------------


def _tenant_predicate(model: type[Any], tenant_id: str) -> Any:
    """Return the mandatory ``tenant_id`` predicate for a tenant-owned model."""
    return model.tenant_id == tenant_id


def _entity_where(
    model: type[Any],
    entity_id: str,
    tenant_id: str | None,
) -> list[Any]:
    """Return the identity predicate, including the tenant when one is required."""
    clauses: list[Any] = [model.id == entity_id]
    if tenant_id is not None:
        clauses.append(_tenant_predicate(model, tenant_id))
    return clauses


async def _fetch_one(
    session: AsyncSession,
    model: type[Any],
    entity_id: str,
    *,
    tenant_id: str | None = None,
    resource_kind: str = "resource",
) -> _Row:
    """Return one visible row or raise the shared not-found error.

    The tenant predicate is part of the SQL, so a row owned by another tenant is
    indistinguishable from a row that does not exist.
    """
    statement = select(model).where(*_entity_where(model, entity_id, tenant_id))
    row = (await session.execute(statement)).scalars().first()
    if row is None:
        raise ResourceNotFoundError(resource_kind, entity_id)
    return row


async def _fetch_optional(
    session: AsyncSession,
    model: type[Any],
    entity_id: str,
    *,
    tenant_id: str | None = None,
) -> _Row | None:
    """Return one visible row, or ``None`` when it is absent or invisible."""
    statement = select(model).where(*_entity_where(model, entity_id, tenant_id))
    return (await session.execute(statement)).scalars().first()


async def _fetch_all(
    session: AsyncSession,
    statement: Select[Any],
) -> list[_Row]:
    """Return every row of a statement as a list."""
    return list((await session.execute(statement)).scalars().all())


def _next_version_values(model: type[Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """Return update values with a version bump and a refreshed ``updated_at``.

    ``updated_at`` is only added for models that declare it; append-only tables
    such as ``run_events`` and ``audit_events`` have no such column.
    """
    updated = dict(values)
    updated["version"] = model.version + 1
    if hasattr(model, "updated_at"):
        updated["updated_at"] = utc_now()
    return updated


async def _apply_compare_and_set(
    session: AsyncSession,
    model: type[Any],
    entity_id: str,
    tenant_id: str | None,
    expected_version: int,
    values: Mapping[str, Any],
    *,
    resource_kind: str,
) -> _Row:
    """Update one row under a compare-and-set predicate and return it.

    The update carries ``WHERE id AND (tenant) AND version = expected`` and
    increments ``version``. Zero affected rows is ambiguous, so existence is
    probed first: a missing or foreign row reports not-found (invisible), while a
    visible row with a different version reports a version conflict. The probe
    predicate is the same one the update uses, so it reveals nothing extra.
    """
    probe = select(model.id).where(*_entity_where(model, entity_id, tenant_id))
    if (await session.execute(probe)).first() is None:
        raise ResourceNotFoundError(resource_kind, entity_id)

    statement = (
        update(model)
        .where(*_entity_where(model, entity_id, tenant_id), model.version == expected_version)
        .values(**_next_version_values(model, values))
        .execution_options(synchronize_session=False)
    )
    affected = (await session.execute(statement)).rowcount
    if not affected:
        logger.warning(
            "repository compare-and-set refused a stale write",
            extra={"resource_kind": resource_kind, "expected_version": expected_version},
        )
        raise VersionConflictError(resource_kind, entity_id, expected_version)
    return await _fetch_one(
        session,
        model,
        entity_id,
        tenant_id=tenant_id,
        resource_kind=resource_kind,
    )


async def _insert_row(session: AsyncSession, row: Any, *, table_label: str) -> None:
    """Insert one row inside a savepoint, converting a race to a typed conflict.

    The savepoint matters: a failed insert would otherwise poison the surrounding
    transaction, so a repository could not report a duplicate and let the caller
    continue with a well-defined outcome.
    """
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        logger.warning(
            "repository insert was rejected by a database constraint",
            extra={"table": table_label},
        )
        raise RunEventSequenceConflictError(
            str(getattr(row, "run_id", "")), int(getattr(row, "sequence", 0))
        ) from exc


# --- Resource catalogue (backs AuthorizationService) ------------------------


@runtime_checkable
class ResourceCatalogLookup(Protocol):
    """Async presence lookup for one resource inside one tenant.

    Unlike :class:`backend.app.auth.authorization.ResourceCatalog` this protocol
    is asynchronous, so it can share the request's async database connection
    instead of opening a second synchronous one.
    """

    async def contains(self, tenant_id: str, resource_kind: str, resource_id: str) -> bool:
        """Return whether the resource is visible to ``tenant_id``."""
        ...


# ``id`` column plus whether the model carries tenant ownership. ``tenants`` is
# the ownership root, so it has no ``tenant_id`` column and is visible only to
# itself here.
_CATALOG_MODELS: dict[str, tuple[type[Any], bool]] = {
    RESOURCE_KIND_TENANT: (Tenant, False),
    RESOURCE_KIND_USER: (User, True),
    RESOURCE_KIND_ROLE: (Role, True),
    RESOURCE_KIND_USER_ROLE_GRANT: (UserRoleGrant, True),
    RESOURCE_KIND_AGENT_RUN: (AgentRun, True),
    RESOURCE_KIND_CHECKPOINT_BINDING: (GraphCheckpointBinding, True),
    RESOURCE_KIND_AUDIT_EVENT: (AuditEvent, True),
}


class UnitOfWorkResourceCatalog:
    """Catalogue bound to one unit of work, sharing its session and transaction.

    This is the preferred implementation: presence is answered inside the same
    transaction and connection that the caller is already using, so a decision
    cannot observe a state the transaction itself has changed.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the catalogue to a unit-of-work session."""
        self._session = session

    async def contains_ref(self, resource_ref: Any) -> bool:
        """Answer presence from a resource reference, matching the sync protocol.

        ``AuthorizationService`` holds the documented synchronous
        ``ResourceCatalog`` contract, which passes one reference. This coroutine
        lets the same service drive this asynchronous, tenant-qualified catalogue
        without opening a second connection or blocking the event loop.
        """
        tenant_id = getattr(resource_ref, "tenant_id", None) or ""
        return await self.contains(
            tenant_id,
            getattr(resource_ref, "kind", ""),
            getattr(resource_ref, "id", ""),
        )

    async def contains(self, tenant_id: str, resource_kind: str, resource_id: str) -> bool:
        """Return whether the resource is visible, never revealing its owner.

        Raises:
            TenantScopeError: when no tenant is supplied.
            UnknownResourceKindError: when the kind is outside the vocabulary.
        """
        tenant = require_tenant(tenant_id, "UnitOfWorkResourceCatalog.contains")
        kind = _require_text(resource_kind, "resource_kind")
        identifier = _require_text(resource_id, "resource_id")
        model, tenant_owned = _catalog_model(kind)
        if not tenant_owned:
            if kind == RESOURCE_KIND_TENANT:
                # A tenant is visible only to itself; another tenant's existence
                # is never confirmed.
                return identifier == tenant
            return False
        statement = select(model.id).where(
            model.id == identifier, _tenant_predicate(model, tenant)
        )
        return (await self._session.execute(statement)).first() is not None


def _catalog_model(resource_kind: str) -> tuple[type[Any], bool]:
    """Return the model and tenant-ownership flag for a resource kind."""
    entry = _CATALOG_MODELS.get(resource_kind)
    if entry is None:
        raise UnknownResourceKindError(resource_kind)
    return entry


def _sync_engine_url(database_url: str) -> str:
    """Rewrite an async URL into the synchronous driver for the catalogue.

    ``AuthorizationService`` requires a *synchronous* ``contains`` so it can be
    used from policy code that is not allowed to await. The URL is therefore
    adapted rather than a second async engine being wrapped in a loop.
    """
    url = make_url(database_url)
    driver = url.drivername
    if driver.endswith("+aiosqlite"):
        return str(url.set(drivername="sqlite"))
    if driver in {"postgresql+psycopg", "postgresql+asyncpg"}:
        return str(url.set(drivername="postgresql+psycopg"))
    return database_url


@lru_cache
def get_resource_catalog_engine() -> Engine:
    """Return the process-wide read-only engine used by the sync catalogue."""
    from backend.app.db.session import build_engine

    settings = get_settings()
    return build_engine(_sync_engine_url(settings.DATABASE_URL), settings.DATABASE_ECHO)


def close_resource_catalog_engine() -> None:
    """Dispose the catalogue engine and drop it from the cache.

    Call at application shutdown; a fresh engine is built on next use.
    """
    get_resource_catalog_engine.cache_clear()


class SqlResourceCatalog:
    """``ResourceCatalog`` implementation backed directly by PostgreSQL.

    Exists for policy code that is synchronous: it opens a short read-only
    session on a dedicated pooled engine, applies the tenant predicate in SQL and
    returns a plain ``bool``. Asynchronous callers should prefer
    :class:`UnitOfWorkResourceCatalog`, which reuses the request's own
    transaction instead of opening a second connection.

    The tenant predicate is applied by the application, so presence is answered
    correctly even on a database role that bypasses RLS.
    """

    def __init__(self, engine: Engine | None = None) -> None:
        """Bind an explicit engine, or resolve the process-wide one lazily."""
        self._engine = engine

    @property
    def engine(self) -> Engine:
        """Return the engine this catalogue reads through."""
        if self._engine is None:
            self._engine = get_resource_catalog_engine()
        return self._engine

    def contains(self, resource_ref: ResourceRef) -> bool:
        """Return whether the caller may observe this resource at all.

        Signatures match :class:`backend.app.auth.authorization.ResourceCatalog`,
        so ``AuthorizationService(resource_catalog=SqlResourceCatalog())`` is
        database-backed. ``False`` is returned for a resource that does not exist
        *and* for one owned by another tenant, which is what makes denials
        non-enumerable.

        Raises:
            UnknownResourceKindError: when ``resource_ref.kind`` is unsupported;
                a typo must not be able to answer ``False`` for a real resource.
        """
        kind = resource_ref.kind
        model, tenant_owned = _catalog_model(kind)
        tenant_id = resource_ref.tenant_id
        with Session(self.engine) as session:
            if not tenant_owned:
                if kind == RESOURCE_KIND_TENANT:
                    return resource_ref.id == tenant_id
                return False
            if not isinstance(tenant_id, str) or not tenant_id.strip():
                # A resource that claims no owner carries no tenant predicate to
                # prove, so it is not observable through this catalogue.
                return False
            statement = select(model.id).where(
                model.id == resource_ref.id, _tenant_predicate(model, tenant_id.strip())
            )
            return session.execute(statement).first() is not None


# --- RLS defence in depth ---------------------------------------------------


def _supports_tenant_context(session: AsyncSession) -> bool:
    """Return whether the bound dialect understands ``set_config``/RLS."""
    try:
        bind = session.get_bind()
    except Exception:  # pragma: no cover - session not bound yet
        return False
    return bind.dialect.name == "postgresql"


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """Publish the tenant to the connection for the current transaction.

    **Defence in depth, not a substitute for the application predicate.** Every
    query in this module still filters on ``tenant_id`` in SQL; this call only
    lets the ``tenant_isolation`` policies reject a query that forgot its
    predicate. Consequences worth stating plainly:

    - It is transaction-local (``set_config(..., true)``), so it disappears at
      commit/rollback and can never leak to the next borrower of a pooled
      connection.
    - It is inert on a role that is a superuser or has ``BYPASSRLS``; on the dev
      compose database the application role is a superuser, so RLS does not
      currently constrain it.
    - It is a no-op on a non-PostgreSQL dialect, which keeps SQLite development
      and isolated tests working.

    Raises:
        TenantScopeError: when no tenant is supplied.
        ValueError: when the session is not bound to a PostgreSQL dialect and the
            caller explicitly asked for RLS enforcement.
    """
    tenant = require_tenant(tenant_id, "set_tenant_context")
    if not _supports_tenant_context(session):
        return
    await session.execute(
        text("select set_config(:setting, :tenant, true)"),
        {"setting": TENANT_CONTEXT_SETTING, "tenant": tenant},
    )


# --- Repositories -----------------------------------------------------------


class TenantRepository:
    """Reads and writes ``tenants`` rows.

    ``tenants`` is the ownership root: it has no ``tenant_id`` column, so
    "tenant scoping" here means *self-visibility*. A member may confirm its own
    tenant; any other tenant id reports the same not-found error as a tenant that
    does not exist.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def get_visible(self, tenant_id: str) -> _Row:
        """Return ``tenant_id`` itself, or raise the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            TenantNotFoundError: when the tenant does not exist.
        """
        tenant = require_tenant(tenant_id, "TenantRepository.get_visible")
        row = await _fetch_optional(self._session, Tenant, tenant)
        if row is None:
            raise TenantNotFoundError(tenant)
        return row

    async def get_by_code(self, code: str) -> _Row:
        """Return the tenant with this operator-facing code, or raise not-found.

        ``code`` is globally unique, so this is an administrative lookup outside
        the tenant predicate; callers must already hold ``cross_tenant_admin``.
        """
        normalized = _require_text(code, "code")
        statement = select(Tenant).where(Tenant.code == normalized)
        row = (await self._session.execute(statement)).scalars().first()
        if row is None:
            raise TenantNotFoundError(normalized)
        return row

    async def exists(self, tenant_id: str) -> bool:
        """Return whether the tenant exists, without raising.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "TenantRepository.exists")
        statement = select(Tenant.id).where(Tenant.id == tenant)
        return (await self._session.execute(statement)).first() is not None

    async def create(self, *, code: str, name: str, status: str = "active") -> _Row:
        """Create a tenant and return the stored row.

        ``tenants`` carries no compare-and-set version, so creation is the only
        write this repository performs; every later change to tenant metadata is
        an administrative operation on a future versioned table.
        """
        row = Tenant(
            code=_require_text(code, "code"),
            name=_require_text(name, "name"),
            status=_require_text(status, "status"),
        )
        self._session.add(row)
        await self._session.flush()
        return row


class UserRepository:
    """Tenant-scoped reads and writes over ``users``."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def get(self, tenant_id: str, user_id: str) -> _Row:
        """Return one user of this tenant, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the user is absent or belongs to another
                tenant.
        """
        tenant = require_tenant(tenant_id, "UserRepository.get")
        return await _fetch_one(
            self._session,
            User,
            _require_text(user_id, "user_id"),
            tenant_id=tenant,
            resource_kind=RESOURCE_KIND_USER,
        )

    async def find_by_external_subject(self, tenant_id: str, external_subject: str) -> _Row | None:
        """Return the user for an identity-provider subject, or ``None``.

        The subject is unique per tenant, so this is the lookup the identity
        adapter uses to prove that an external subject has not moved tenants.
        """
        tenant = require_tenant(tenant_id, "UserRepository.find_by_external_subject")
        subject = _require_text(external_subject, "external_subject")
        statement = select(User).where(
            _tenant_predicate(User, tenant), User.external_subject == subject
        )
        return (await self._session.execute(statement)).scalars().first()

    async def find_by_username(self, tenant_id: str, username: str) -> _Row | None:
        """Return the member with ``username`` inside this tenant, or ``None``.

        Usernames are unique per tenant after the enforce phase, so this lookup is
        only meaningful with an explicit tenant and must never be issued without
        one: the same username can legitimately exist in several tenants.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "UserRepository.find_by_username")
        statement = select(User).where(
            _tenant_predicate(User, tenant), User.username == _require_text(username, "username")
        )
        return (await self._session.execute(statement)).scalars().first()

    async def create(
        self,
        tenant_id: str,
        *,
        username: str,
        email: str,
        password_hash: str,
        display_name: str,
        external_subject: str | None = None,
        department_id: str | None = None,
        status: str = "active",
    ) -> _Row:
        """Create a member of this tenant.

        ``tenant_id`` on the row is taken from the enforced parameter, never from
        the keyword arguments, so a caller cannot create a user in another
        tenant by passing inconsistent input.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "UserRepository.create")
        row = User(
            tenant_id=tenant,
            username=_require_text(username, "username"),
            email=_require_text(email, "email"),
            password_hash=_require_text(password_hash, "password_hash"),
            display_name=_require_text(display_name, "display_name"),
            external_subject=external_subject,
            department_id=department_id,
            status=_require_text(status, "status"),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_status(
        self,
        tenant_id: str,
        user_id: str,
        status: str,
        expected_version: int,
    ) -> _Row:
        """Change a user's lifecycle status under compare-and-set.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the user is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "UserRepository.set_status")
        return await _apply_compare_and_set(
            self._session,
            User,
            _require_text(user_id, "user_id"),
            tenant,
            require_version(expected_version, "UserRepository.set_status"),
            {"status": _require_text(status, "status")},
            resource_kind=RESOURCE_KIND_USER,
        )


class RoleRepository:
    """Tenant-scoped reads and versioned writes over ``roles``."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def get(self, tenant_id: str, role_id: str) -> _Row:
        """Return one role of this tenant, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the role is absent or foreign.
        """
        tenant = require_tenant(tenant_id, "RoleRepository.get")
        return await _fetch_one(
            self._session,
            Role,
            _require_text(role_id, "role_id"),
            tenant_id=tenant,
            resource_kind=RESOURCE_KIND_ROLE,
        )

    async def get_by_code(self, tenant_id: str, code: str) -> _Row:
        """Return one role by its per-tenant code, or the shared not-found error.

        Two tenants may legitimately both define ``admin``, so the code alone is
        never enough: the tenant predicate is what keeps them apart.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when no such role is visible in this tenant.
        """
        tenant = require_tenant(tenant_id, "RoleRepository.get_by_code")
        normalized = _require_text(code, "code")
        statement = select(Role).where(_tenant_predicate(Role, tenant), Role.code == normalized)
        row = (await self._session.execute(statement)).scalars().first()
        if row is None:
            raise ResourceNotFoundError(RESOURCE_KIND_ROLE, normalized)
        return row

    async def list_for_tenant(self, tenant_id: str) -> list[_Row]:
        """Return every role defined in this tenant, ordered by code.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "RoleRepository.list_for_tenant")
        statement = (
            select(Role).where(_tenant_predicate(Role, tenant)).order_by(Role.code)
        )
        return await _fetch_all(self._session, statement)

    async def create(
        self,
        tenant_id: str,
        *,
        code: str,
        name: str,
        actions: Iterable[str] = (),
        description: str = "",
    ) -> _Row:
        """Create a role holding an allow-only action allowlist.

        The allowlist is normalised to a sorted, de-duplicated list so that a
        byte-for-byte comparison of two roles with the same meaning is possible.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when an action code is empty.
        """
        tenant = require_tenant(tenant_id, "RoleRepository.create")
        row = Role(
            tenant_id=tenant,
            code=_require_text(code, "code"),
            name=_require_text(name, "name"),
            description=description,
            actions=_normalise_actions(actions),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def replace_actions(
        self,
        tenant_id: str,
        role_id: str,
        actions: Iterable[str],
        expected_version: int,
    ) -> _Row:
        """Replace a role's action allowlist under compare-and-set.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the role is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
            ValueError: when an action code is empty.
        """
        tenant = require_tenant(tenant_id, "RoleRepository.replace_actions")
        return await _apply_compare_and_set(
            self._session,
            Role,
            _require_text(role_id, "role_id"),
            tenant,
            require_version(expected_version, "RoleRepository.replace_actions"),
            {"actions": _normalise_actions(actions)},
            resource_kind=RESOURCE_KIND_ROLE,
        )


def _normalise_actions(actions: Iterable[str]) -> list[str]:
    """Return the allowlist as a sorted, de-duplicated list of action codes."""
    if isinstance(actions, str):
        raise ValueError("actions must be a collection of codes, not a string")
    return sorted({_require_text(action, "action") for action in actions})


class UserRoleGrantRepository:
    """Tenant-scoped reads and writes over ``user_role_grants``.

    This repository is what makes authorization *fresh*: ``active_grants``
    re-reads the currently valid windows instead of trusting the scopes carried
    by a principal snapshot, so a revocation or expiry takes effect on the next
    authorization check.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def get(self, tenant_id: str, grant_id: str) -> _Row:
        """Return one grant of this tenant, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the grant is absent or foreign.
        """
        tenant = require_tenant(tenant_id, "UserRoleGrantRepository.get")
        return await _fetch_one(
            self._session,
            UserRoleGrant,
            _require_text(grant_id, "grant_id"),
            tenant_id=tenant,
            resource_kind=RESOURCE_KIND_USER_ROLE_GRANT,
        )

    async def create(
        self,
        tenant_id: str,
        *,
        user_id: str,
        role_id: str,
        scope: str = "tenant",
        valid_from: datetime | None = None,
        expires_at: datetime | None = None,
        granted_by: str | None = None,
    ) -> _Row:
        """Grant a role to a member over an explicit validity window.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when the window is empty or already closed.
        """
        tenant = require_tenant(tenant_id, "UserRoleGrantRepository.create")
        start = _as_utc(valid_from) if valid_from is not None else utc_now()
        end = _as_utc(expires_at) if expires_at is not None else None
        if end is not None and end <= start:
            raise ValueError("expires_at must be later than valid_from")
        row = UserRoleGrant(
            tenant_id=tenant,
            user_id=_require_text(user_id, "user_id"),
            role_id=_require_text(role_id, "role_id"),
            scope=_require_text(scope, "scope"),
            valid_from=start,
            expires_at=end,
            granted_by=granted_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def active_grants(
        self,
        tenant_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> list[_Row]:
        """Return the grants a member may actually use at ``now``.

        A grant counts only while ``valid_from <= now``, ``expires_at IS NULL OR
        expires_at > now`` and ``revoked_at IS NULL``. This is the query behind
        fresh authorization, so the time window is evaluated by the database
        rather than by the caller's snapshot.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "UserRoleGrantRepository.active_grants")
        member = _require_text(user_id, "user_id")
        moment = _as_utc(now) if now is not None else utc_now()
        statement = (
            select(UserRoleGrant)
            .where(
                _tenant_predicate(UserRoleGrant, tenant),
                UserRoleGrant.user_id == member,
                UserRoleGrant.valid_from <= moment,
                UserRoleGrant.revoked_at.is_(None),
                # A grant without an expiry is permanent, so the null case must be
                # spelled out. Collapsing it with COALESCE(expires_at, moment)
                # compares the moment with itself and silently excludes every
                # perpetual grant, which denies all authorization outright.
                (UserRoleGrant.expires_at.is_(None))
                | (UserRoleGrant.expires_at > moment),
            )
            .order_by(UserRoleGrant.valid_from, UserRoleGrant.id)
        )
        return await _fetch_all(self._session, statement)

    async def active_role_codes(
        self,
        tenant_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> list[str]:
        """Return the codes of the roles this member currently holds.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "UserRoleGrantRepository.active_role_codes")
        member = _require_text(user_id, "user_id")
        moment = _as_utc(now) if now is not None else utc_now()
        statement = (
            select(Role.code)
            .join(UserRoleGrant, UserRoleGrant.role_id == Role.id)
            .where(
                _tenant_predicate(UserRoleGrant, tenant),
                _tenant_predicate(Role, tenant),
                UserRoleGrant.user_id == member,
                UserRoleGrant.valid_from <= moment,
                UserRoleGrant.revoked_at.is_(None),
                # A grant without an expiry is permanent, so the null case must be
                # spelled out. Collapsing it with COALESCE(expires_at, moment)
                # compares the moment with itself and silently excludes every
                # perpetual grant, which denies all authorization outright.
                (UserRoleGrant.expires_at.is_(None))
                | (UserRoleGrant.expires_at > moment),
            )
            .order_by(Role.code)
        )
        return [str(code) for code in (await self._session.execute(statement)).scalars()]

    async def revoke(
        self,
        tenant_id: str,
        grant_id: str,
        *,
        revoked_by: str,
        reason: str | None = None,
        expected_version: int,
    ) -> _Row:
        """Revoke a grant under compare-and-set.

        Revoking writes ``revoked_at`` instead of deleting the row: the audit
        trail keeps who held what, while :meth:`active_grants` immediately stops
        honouring it.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the grant is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "UserRoleGrantRepository.revoke")
        return await _apply_compare_and_set(
            self._session,
            UserRoleGrant,
            _require_text(grant_id, "grant_id"),
            tenant,
            require_version(expected_version, "UserRoleGrantRepository.revoke"),
            {
                "revoked_at": utc_now(),
                "revoked_by": _require_text(revoked_by, "revoked_by"),
                "revocation_reason": reason,
            },
            resource_kind=RESOURCE_KIND_USER_ROLE_GRANT,
        )


class AgentRunRepository:
    """Tenant-scoped reads and compare-and-set writes over ``agent_runs``.

    ``run_id`` is the public correlation identifier and never the primary key, so
    every lookup filters on both tenant and ``run_id``.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def get(self, tenant_id: str, run_id: str) -> _Row:
        """Return one run of this tenant, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the run is absent or belongs to another
                tenant.
        """
        tenant = require_tenant(tenant_id, "AgentRunRepository.get")
        return await self._fetch_run_by_public_id(tenant, run_id, required=True)

    async def find(self, tenant_id: str, run_id: str) -> _Row | None:
        """Return one run of this tenant, or ``None`` when it is not visible."""
        tenant = require_tenant(tenant_id, "AgentRunRepository.find")
        return await self._fetch_run_by_public_id(tenant, run_id, required=False)

    async def _fetch_run_by_public_id(
        self,
        tenant_id: str,
        run_id: str,
        *,
        required: bool,
    ) -> _Row | None:
        """Load a run by its public identifier under the tenant predicate."""
        public_id = _require_text(run_id, "run_id")
        statement = select(AgentRun).where(
            _tenant_predicate(AgentRun, tenant_id), AgentRun.run_id == public_id
        )
        row = (await self._session.execute(statement)).scalars().first()
        if row is None and required:
            raise ResourceNotFoundError(RESOURCE_KIND_AGENT_RUN, public_id)
        return row

    async def create(
        self,
        tenant_id: str,
        *,
        user_id: str,
        kind: str,
        thread_id: str,
        run_id: str | None = None,
        conversation_id: str | None = None,
        status: str = "queued",
        input_snapshot: Mapping[str, Any] | None = None,
        evidence_gate: str = "not_applicable",
        deadline_at: datetime | None = None,
        graph_version: str = "1",
    ) -> _Row:
        """Create a queued run for this tenant.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when a supplied status is outside the run state machine.
        """
        tenant = require_tenant(tenant_id, "AgentRunRepository.create")
        resolved_status = _require_run_status(status)
        row = AgentRun(
            run_id=run_id or new_id(),
            tenant_id=tenant,
            user_id=_require_text(user_id, "user_id"),
            conversation_id=conversation_id,
            kind=_require_text(kind, "kind"),
            thread_id=_require_text(thread_id, "thread_id"),
            status=resolved_status,
            input_snapshot=dict(input_snapshot or {}),
            evidence_gate=_require_text(evidence_gate, "evidence_gate"),
            deadline_at=_as_utc(deadline_at) if deadline_at is not None else None,
            graph_version=_require_text(graph_version, "graph_version"),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_status(
        self,
        tenant_id: str,
        run_id: str,
        status: str,
        expected_version: int,
    ) -> _Row:
        """Transition a run under compare-and-set, honouring the state machine.

        Terminal states cannot be left by an ordinary update: recovering a
        finished run is an audited administrative action that creates a new run.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the run is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
            RunStateTransitionError: when the run is already terminal.
            ValueError: when ``status`` is outside the run state machine.
        """
        tenant = require_tenant(tenant_id, "AgentRunRepository.set_status")
        public_id = _require_text(run_id, "run_id")
        target = _require_run_status(status)
        current = await self._fetch_run_by_public_id(tenant, public_id, required=True)
        assert current is not None  # required=True guarantees a row or an error
        if current.status in TERMINAL_RUN_STATUSES:
            raise RunStateTransitionError(
                "RUN_STATE_TRANSITION_INVALID",
                f"run {_printable(public_id)} is terminal and cannot be resumed in place",
                409,
                {"run_id": _printable(public_id), "status": current.status},
            )
        values: dict[str, Any] = {"status": target}
        if target == "running" and current.started_at is None:
            values["started_at"] = utc_now()
        if target in TERMINAL_RUN_STATUSES:
            values["finished_at"] = utc_now()
        return await self._update_by_public_id(tenant, public_id, expected_version, values)

    async def set_evidence_gate(
        self,
        tenant_id: str,
        run_id: str,
        evidence_gate: str,
        expected_version: int,
    ) -> _Row:
        """Record the run's evidence gate under compare-and-set.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the run is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "AgentRunRepository.set_evidence_gate")
        return await self._update_by_public_id(
            tenant,
            _require_text(run_id, "run_id"),
            expected_version,
            {"evidence_gate": _require_text(evidence_gate, "evidence_gate")},
        )

    async def set_result_snapshot(
        self,
        tenant_id: str,
        run_id: str,
        result_snapshot: Mapping[str, Any],
        expected_version: int,
    ) -> _Row:
        """Replace the run's sanitized result snapshot under compare-and-set.

        The snapshot is persisted exactly as supplied: file bytes, credentials and
        raw provider payloads must have been removed by the caller, because this
        layer never inspects payload contents.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the run is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "AgentRunRepository.set_result_snapshot")
        return await self._update_by_public_id(
            tenant,
            _require_text(run_id, "run_id"),
            expected_version,
            {"result_snapshot": dict(result_snapshot)},
        )

    async def _update_by_public_id(
        self,
        tenant_id: str,
        run_id: str,
        expected_version: object,
        values: Mapping[str, Any],
    ) -> _Row:
        """Run a compare-and-set update keyed by ``run_id`` instead of ``id``."""
        version = require_version(expected_version, "AgentRunRepository update")
        probe = select(AgentRun.id).where(
            _tenant_predicate(AgentRun, tenant_id), AgentRun.run_id == run_id
        )
        if (await self._session.execute(probe)).first() is None:
            raise ResourceNotFoundError(RESOURCE_KIND_AGENT_RUN, run_id)
        statement = (
            update(AgentRun)
            .where(
                _tenant_predicate(AgentRun, tenant_id),
                AgentRun.run_id == run_id,
                AgentRun.version == version,
            )
            .values(**_next_version_values(AgentRun, values))
            .execution_options(synchronize_session=False)
        )
        affected = (await self._session.execute(statement)).rowcount
        if not affected:
            logger.warning(
                "repository compare-and-set refused a stale write",
                extra={"resource_kind": RESOURCE_KIND_AGENT_RUN, "expected_version": version},
            )
            raise VersionConflictError(RESOURCE_KIND_AGENT_RUN, run_id, version)
        row = await self._fetch_run_by_public_id(tenant_id, run_id, required=True)
        assert row is not None  # required=True guarantees a row or an error
        return row

    async def list_for_tenant(self, tenant_id: str, *, limit: int = 100) -> list[_Row]:
        """Return the tenant's runs, newest first, bounded by ``limit``.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when ``limit`` is not positive.
        """
        tenant = require_tenant(tenant_id, "AgentRunRepository.list_for_tenant")
        statement = (
            select(AgentRun)
            .where(_tenant_predicate(AgentRun, tenant))
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .limit(_require_limit(limit))
        )
        return await _fetch_all(self._session, statement)


def _require_run_status(status: object) -> str:
    """Return a valid ``AgentRun`` status or raise a loud ``ValueError``."""
    resolved = _require_text(status, "status")
    if resolved not in RUN_STATUSES:
        raise ValueError(f"status {resolved!r} is not a valid run status")
    return resolved


def _require_limit(limit: object) -> int:
    """Return a positive row limit."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return limit


class RunEventRepository:
    """Append-only access to ``run_events``.

    Events are append-only by design: there is no update or delete method, and
    ``(run_id, sequence)`` is unique so a resumed stream replays in order without
    gaps or repeats.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def append(
        self,
        tenant_id: str,
        run_id: str,
        event_type: str,
        *,
        sequence: int | None = None,
        payload: Mapping[str, Any] | None = None,
        stage: str = "",
        public_status: str = "",
        trace_id: str | None = None,
        request_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> _Row:
        """Append one durable run event and return the stored row.

        When ``sequence`` is omitted the next free number is derived from the
        current maximum inside this transaction; when it is supplied it is used
        verbatim so a producer that owns its own numbering can be checked against
        the unique ``(run_id, sequence)`` constraint. Either way a duplicate fails
        with :class:`RunEventSequenceConflictError` rather than overwriting or
        silently renumbering an existing event.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when a supplied ``sequence`` is not positive.
            RunEventSequenceConflictError: when ``(run_id, sequence)`` is taken.
        """
        tenant = require_tenant(tenant_id, "RunEventRepository.append")
        public_run_id = _require_text(run_id, "run_id")
        if sequence is None:
            resolved_sequence = await self.next_sequence(tenant, public_run_id)
        else:
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise ValueError("sequence must be a positive integer")
            resolved_sequence = sequence
        row = RunEvent(
            tenant_id=tenant,
            run_id=public_run_id,
            sequence=resolved_sequence,
            event_type=_require_text(event_type, "event_type"),
            stage=stage,
            public_status=public_status,
            payload=dict(payload or {}),
            occurred_at=_as_utc(occurred_at) if occurred_at is not None else utc_now(),
            trace_id=trace_id,
            request_id=request_id,
        )
        await _insert_row(self._session, row, table_label="run_events")
        return row

    async def next_sequence(self, tenant_id: str, run_id: str) -> int:
        """Return the next free sequence number for a run.

        The result is advisory: two concurrent appenders can compute the same
        value, and the unique constraint is what actually arbitrates.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "RunEventRepository.next_sequence")
        statement = select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
            _tenant_predicate(RunEvent, tenant), RunEvent.run_id == _require_text(run_id, "run_id")
        )
        highest = await self._session.scalar(statement)
        return int(highest or 0) + 1

    async def list_for_run(
        self,
        tenant_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[_Row]:
        """Return a run's events in ascending sequence order.

        ``after_sequence`` supports resuming an interrupted stream; ``limit``
        bounds a single page so a long run cannot exhaust memory.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when ``after_sequence`` is negative or ``limit`` is not
                positive.
        """
        tenant = require_tenant(tenant_id, "RunEventRepository.list_for_run")
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int):
            raise ValueError("after_sequence must be an integer")
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        statement = (
            select(RunEvent)
            .where(
                _tenant_predicate(RunEvent, tenant),
                RunEvent.run_id == _require_text(run_id, "run_id"),
                RunEvent.sequence > after_sequence,
            )
            .order_by(RunEvent.sequence)
            .limit(_require_limit(limit))
        )
        return await _fetch_all(self._session, statement)

    async def count_for_run(self, tenant_id: str, run_id: str) -> int:
        """Return how many events this tenant has recorded for a run.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        tenant = require_tenant(tenant_id, "RunEventRepository.count_for_run")
        statement = (
            select(func.count())
            .select_from(RunEvent)
            .where(
                _tenant_predicate(RunEvent, tenant),
                RunEvent.run_id == _require_text(run_id, "run_id"),
            )
        )
        return int(await self._session.scalar(statement) or 0)


class GraphCheckpointBindingRepository:
    """Tenant-scoped access to ``graph_checkpoint_bindings``.

    A caller must resolve a binding before it may address an opaque LangGraph
    thread, which is what keeps a thread identifier from becoming an
    authorization bypass.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def get(self, tenant_id: str, binding_id: str) -> _Row:
        """Return one binding of this tenant, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the binding is absent or foreign.
        """
        tenant = require_tenant(tenant_id, "GraphCheckpointBindingRepository.get")
        return await _fetch_one(
            self._session,
            GraphCheckpointBinding,
            _require_text(binding_id, "binding_id"),
            tenant_id=tenant,
            resource_kind=RESOURCE_KIND_CHECKPOINT_BINDING,
        )

    async def resolve_thread(
        self,
        tenant_id: str,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> _Row:
        """Return the authorized binding for a run's thread, or raise not-found.

        The binding is unique per tenant, so a thread identifier that belongs to
        another tenant is reported exactly like a run that does not exist.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when no visible binding matches.
        """
        tenant = require_tenant(tenant_id, "GraphCheckpointBindingRepository.resolve_thread")
        clauses: list[Any] = [
            _tenant_predicate(GraphCheckpointBinding, tenant),
            GraphCheckpointBinding.run_id == _require_text(run_id, "run_id"),
        ]
        if user_id is not None:
            clauses.append(GraphCheckpointBinding.user_id == _require_text(user_id, "user_id"))
        row = (
            await self._session.execute(select(GraphCheckpointBinding).where(*clauses))
        ).scalars().first()
        if row is None:
            raise ResourceNotFoundError(RESOURCE_KIND_CHECKPOINT_BINDING, run_id)
        return row

    async def create(
        self,
        tenant_id: str,
        *,
        user_id: str,
        run_id: str,
        thread_id: str,
        graph_schema_version: str = "1",
        checkpoint_schema_version: str = "1",
        authorization_version: int = 1,
    ) -> _Row:
        """Bind one opaque thread to a tenant, user and run.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when ``authorization_version`` is negative.
        """
        tenant = require_tenant(tenant_id, "GraphCheckpointBindingRepository.create")
        if isinstance(authorization_version, bool) or not isinstance(authorization_version, int):
            raise ValueError("authorization_version must be an integer")
        if authorization_version < 0:
            raise ValueError("authorization_version must not be negative")
        row = GraphCheckpointBinding(
            tenant_id=tenant,
            user_id=_require_text(user_id, "user_id"),
            run_id=_require_text(run_id, "run_id"),
            thread_id=_require_text(thread_id, "thread_id"),
            graph_schema_version=_require_text(graph_schema_version, "graph_schema_version"),
            checkpoint_schema_version=_require_text(
                checkpoint_schema_version, "checkpoint_schema_version"
            ),
            authorization_version=authorization_version,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def record_checkpoint(
        self,
        tenant_id: str,
        binding_id: str,
        *,
        latest_checkpoint_id: str,
        expected_version: int,
    ) -> _Row:
        """Record the newest checkpoint id under compare-and-set.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the binding is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "GraphCheckpointBindingRepository.record_checkpoint")
        return await _apply_compare_and_set(
            self._session,
            GraphCheckpointBinding,
            _require_text(binding_id, "binding_id"),
            tenant,
            require_version(expected_version, "GraphCheckpointBindingRepository.record_checkpoint"),
            {"latest_checkpoint_id": _require_text(latest_checkpoint_id, "latest_checkpoint_id")},
            resource_kind=RESOURCE_KIND_CHECKPOINT_BINDING,
        )

    async def set_authorization_version(
        self,
        tenant_id: str,
        binding_id: str,
        authorization_version: int,
        expected_version: int,
    ) -> _Row:
        """Re-pin the binding to the authorization version that may resume it.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the binding is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
            ValueError: when ``authorization_version`` is negative.
        """
        tenant = require_tenant(
            tenant_id, "GraphCheckpointBindingRepository.set_authorization_version"
        )
        if isinstance(authorization_version, bool) or not isinstance(authorization_version, int):
            raise ValueError("authorization_version must be an integer")
        if authorization_version < 0:
            raise ValueError("authorization_version must not be negative")
        return await _apply_compare_and_set(
            self._session,
            GraphCheckpointBinding,
            _require_text(binding_id, "binding_id"),
            tenant,
            require_version(
                expected_version,
                "GraphCheckpointBindingRepository.set_authorization_version",
            ),
            {"authorization_version": authorization_version},
            resource_kind=RESOURCE_KIND_CHECKPOINT_BINDING,
        )


class ClaimOutcome(StrEnum):
    """How an idempotency claim was resolved."""

    CLAIMED = "claimed"
    """This caller owns the key and must execute the operation."""

    IN_PROGRESS = "in_progress"
    """An identical request is already running; join it instead of duplicating."""

    REPLAYED = "replayed"
    """The identical request already finished; reuse its canonical response."""


class IdempotencyClaim:
    """Result of claiming one idempotency key.

    ``record`` is always the single authoritative row for the key, so an
    in-progress duplicate observes the original operation's state rather than
    starting a second one.
    """

    __slots__ = ("outcome", "record")

    def __init__(self, outcome: ClaimOutcome, record: _Row) -> None:
        """Bind an outcome to the authoritative record."""
        self.outcome = outcome
        self.record = record

    @property
    def is_owner(self) -> bool:
        """Return whether this caller must execute the operation."""
        return self.outcome is ClaimOutcome.CLAIMED

    @property
    def in_progress(self) -> bool:
        """Return whether the operation is still running elsewhere."""
        return self.outcome is ClaimOutcome.IN_PROGRESS

    @property
    def replayed(self) -> bool:
        """Return whether a canonical response can be replayed."""
        return self.outcome is ClaimOutcome.REPLAYED

    def __repr__(self) -> str:
        """Return a compact repr that never includes the request digest."""
        return f"IdempotencyClaim(outcome={self.outcome.value!r})"


class IdempotencyRepository:
    """Claims and settles idempotency keys for client and connector calls.

    One key is bound to exactly one request digest for its lifetime, so a retry
    of the same body joins or replays the original operation while a *different*
    body under the same key is refused instead of executed twice.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def claim(
        self,
        tenant_id: str,
        *,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        expires_at: datetime | None = None,
    ) -> IdempotencyClaim:
        """Claim the key, or resolve it against the existing record.

        Behaviour by case:

        - no record: insert an ``in_progress`` record and return
          :attr:`ClaimOutcome.CLAIMED`;
        - same digest, still ``in_progress``: return
          :attr:`ClaimOutcome.IN_PROGRESS` with the original row, so the caller
          joins the running operation;
        - same digest, settled (or expired): return
          :attr:`ClaimOutcome.REPLAYED` and re-arm the record when the previous
          attempt is no longer valid;
        - different digest: raise :class:`IdempotencyConflictError`.

        The insert runs inside a savepoint, so two racing callers cannot both
        claim: the loser of the unique ``(tenant_id, operation, idempotency_key)``
        race re-reads the winner's row and resolves against it.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            IdempotencyConflictError: when the key is bound to another digest.
        """
        tenant = require_tenant(tenant_id, "IdempotencyRepository.claim")
        resolved_operation = _require_text(operation, "operation")
        key = _require_text(idempotency_key, "idempotency_key")
        digest = _require_text(request_digest, "request_digest")
        window_end = _as_utc(expires_at) if expires_at is not None else None
        now = utc_now()

        existing = await self._find(tenant, resolved_operation, key)
        if existing is not None:
            return await self._resolve_existing(existing, digest, now, window_end)

        row = IdempotencyRecord(
            tenant_id=tenant,
            operation=resolved_operation,
            idempotency_key=key,
            request_digest=digest,
            state=IDEMPOTENCY_STATE_IN_PROGRESS,
            expires_at=window_end,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # Lost the insert race: the winner's row is authoritative, so resolve
            # against it instead of starting a second logical operation.
            winner = await self._find(tenant, resolved_operation, key)
            if winner is None:  # pragma: no cover - row vanished between statements
                raise
            return await self._resolve_existing(winner, digest, now, window_end)
        return IdempotencyClaim(ClaimOutcome.CLAIMED, row)

    async def _resolve_existing(
        self,
        existing: _Row,
        digest: str,
        now: datetime,
        window_end: datetime | None,
    ) -> IdempotencyClaim:
        """Resolve a claim against the record already stored for the key."""
        if existing.request_digest != digest:
            raise IdempotencyConflictError(existing.operation, existing.idempotency_key)
        expired = existing.expires_at is not None and _as_utc(existing.expires_at) <= now
        if existing.state == IDEMPOTENCY_STATE_IN_PROGRESS and not expired:
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS, existing)
        # A settled record is replayed; an expired one is re-armed so a retry
        # after the retention window can make progress again.
        updated = await self._settle(
            existing,
            state=IDEMPOTENCY_STATE_IN_PROGRESS,
            response_status=None,
            response_body=None,
            expires_at=window_end,
        )
        outcome = ClaimOutcome.REPLAYED if not expired else ClaimOutcome.CLAIMED
        return IdempotencyClaim(outcome, updated)

    async def _settle(
        self,
        existing: _Row,
        *,
        state: str,
        response_status: int | None,
        response_body: Mapping[str, Any] | None,
        expires_at: datetime | None,
    ) -> _Row:
        """Move a record to a new state under compare-and-set on its version."""
        statement = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.id == existing.id,
                IdempotencyRecord.tenant_id == existing.tenant_id,
                IdempotencyRecord.version == existing.version,
            )
            .values(
                state=state,
                response_status=response_status,
                response_body=None if response_body is None else dict(response_body),
                expires_at=expires_at,
                updated_at=utc_now(),
                version=IdempotencyRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if not (await self._session.execute(statement)).rowcount:
            raise VersionConflictError(
                "idempotency_record", existing.id, int(existing.version)
            )
        refreshed = await _fetch_one(
            self._session,
            IdempotencyRecord,
            existing.id,
            tenant_id=existing.tenant_id,
            resource_kind="idempotency_record",
        )
        return refreshed

    async def _find(self, tenant_id: str, operation: str, key: str) -> _Row | None:
        """Return the unique record for a key inside one tenant."""
        statement = select(IdempotencyRecord).where(
            _tenant_predicate(IdempotencyRecord, tenant_id),
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == key,
        )
        return (await self._session.execute(statement)).scalars().first()

    async def get(self, tenant_id: str, record_id: str) -> _Row:
        """Return one idempotency record, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the record is absent or foreign.
        """
        tenant = require_tenant(tenant_id, "IdempotencyRepository.get")
        return await _fetch_one(
            self._session,
            IdempotencyRecord,
            _require_text(record_id, "record_id"),
            tenant_id=tenant,
            resource_kind="idempotency_record",
        )

    async def complete(
        self,
        tenant_id: str,
        record_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any] | None = None,
        expected_version: int,
    ) -> _Row:
        """Mark a claimed operation as completed with its canonical response.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the record is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "IdempotencyRepository.complete")
        return await _apply_compare_and_set(
            self._session,
            IdempotencyRecord,
            _require_text(record_id, "record_id"),
            tenant,
            require_version(expected_version, "IdempotencyRepository.complete"),
            {
                "state": IDEMPOTENCY_STATE_COMPLETED,
                "response_status": response_status,
                "response_body": None if response_body is None else dict(response_body),
            },
            resource_kind="idempotency_record",
        )

    async def fail(
        self,
        tenant_id: str,
        record_id: str,
        *,
        expected_version: int,
    ) -> _Row:
        """Mark a claimed operation as failed so a retry may re-arm it.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the record is absent or foreign.
            VersionConflictError: when ``expected_version`` is stale.
        """
        tenant = require_tenant(tenant_id, "IdempotencyRepository.fail")
        return await _apply_compare_and_set(
            self._session,
            IdempotencyRecord,
            _require_text(record_id, "record_id"),
            tenant,
            require_version(expected_version, "IdempotencyRepository.fail"),
            {"state": IDEMPOTENCY_STATE_FAILED},
            resource_kind="idempotency_record",
        )


class AuditEventRepository:
    """Append-only access to ``audit_events``.

    Audit rows are never edited in place. ``append`` is idempotent by
    ``event_id``, so a retried write of the same event reports the stored row
    instead of creating a duplicate.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def append(
        self,
        tenant_id: str,
        *,
        event_id: str,
        actor_ref: str,
        action: str,
        outcome: str,
        resource_kind: str = "",
        resource_id: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        authorization_version: int = 1,
        reason_code: str | None = None,
        redacted_metadata: Mapping[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> tuple[_Row, bool]:
        """Append one audit event and report whether it was newly written.

        Returns:
            ``(row, created)`` where ``created`` is ``False`` when the event id
            already existed and the stored row was returned unchanged.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when ``authorization_version`` is negative.
        """
        tenant = require_tenant(tenant_id, "AuditEventRepository.append")
        identifier = _require_text(event_id, "event_id")
        existing = await self._find_by_event_id(tenant, identifier)
        if existing is not None:
            return existing, False
        if isinstance(authorization_version, bool) or not isinstance(authorization_version, int):
            raise ValueError("authorization_version must be an integer")
        if authorization_version < 0:
            raise ValueError("authorization_version must not be negative")
        row = AuditEvent(
            event_id=identifier,
            tenant_id=tenant,
            run_id=run_id,
            request_id=request_id,
            trace_id=trace_id,
            actor_ref=_require_text(actor_ref, "actor_ref"),
            authorization_version=authorization_version,
            action=_require_text(action, "action"),
            resource_kind=resource_kind,
            resource_id=resource_id,
            outcome=_require_text(outcome, "outcome"),
            reason_code=reason_code,
            redacted_metadata=dict(redacted_metadata or {}),
            occurred_at=_as_utc(occurred_at) if occurred_at is not None else utc_now(),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # A concurrent writer stored the same event id first; its row is the
            # single audit record for that event.
            winner = await self._find_by_event_id(tenant, identifier)
            if winner is None:  # pragma: no cover - row vanished between statements
                raise
            return winner, False
        return row, True

    async def get(self, tenant_id: str, event_id: str) -> _Row:
        """Return one audit event of this tenant, or the shared not-found error.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when the event is absent or foreign.
        """
        tenant = require_tenant(tenant_id, "AuditEventRepository.get")
        return await _fetch_one(
            self._session,
            AuditEvent,
            _require_text(event_id, "event_id"),
            tenant_id=tenant,
            resource_kind=RESOURCE_KIND_AUDIT_EVENT,
        )

    async def list_for_run(
        self,
        tenant_id: str,
        run_id: str,
        *,
        limit: int = 200,
    ) -> list[_Row]:
        """Return a run's audit events in the order they occurred.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when ``limit`` is not positive.
        """
        tenant = require_tenant(tenant_id, "AuditEventRepository.list_for_run")
        statement = (
            select(AuditEvent)
            .where(
                _tenant_predicate(AuditEvent, tenant),
                AuditEvent.run_id == _require_text(run_id, "run_id"),
            )
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
            .limit(_require_limit(limit))
        )
        return await _fetch_all(self._session, statement)

    async def _find_by_event_id(self, tenant_id: str, event_id: str) -> _Row | None:
        """Return the stored audit row for an event id inside one tenant."""
        statement = select(AuditEvent).where(
            AuditEvent.event_id == event_id, _tenant_predicate(AuditEvent, tenant_id)
        )
        return (await self._session.execute(statement)).scalars().first()


async def append_audit_event(
    session: AsyncSession,
    tenant_id: str,
    *,
    event_id: str | None = None,
    actor_ref: str,
    action: str,
    outcome: str,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    resource_kind: str = "",
    resource_id: str | None = None,
    reason_code: str | None = None,
    authorization_version: int = 1,
    redacted_metadata: Mapping[str, Any] | None = None,
) -> _Row:
    """Append an audit event on a raw session, generating the event id if omitted.

    Provided because audit is written from every boundary — including graph nodes
    that already hold a session and must record a state transition in the *same*
    transaction as the change it describes (see cross-entity invariant 10).

    Raises:
        TenantScopeError: when ``tenant_id`` is empty or missing.
    """
    tenant = require_tenant(tenant_id, "append_audit_event")
    row, _created = await AuditEventRepository(session).append(
        tenant,
        event_id=event_id or new_id(),
        actor_ref=actor_ref,
        action=action,
        outcome=outcome,
        resource_kind=resource_kind,
        resource_id=resource_id,
        run_id=run_id,
        request_id=request_id,
        trace_id=trace_id,
        authorization_version=authorization_version,
        reason_code=reason_code,
        redacted_metadata=redacted_metadata,
    )
    return row


# --- Unit of work -----------------------------------------------------------


class UnitOfWork:
    """One transaction, one session and the repositories that share both.

    A unit of work is not shared between tasks: it owns its ``AsyncSession`` and
    therefore its transaction, so a failure rolls back exactly the work it
    performed. Repositories are created lazily on first access and always receive
    that same session, which is what makes a multi-repository change atomic.

    Use it as an async context manager, which rolls back on error and on early
    exit and never commits implicitly:

    ```python
    async with UnitOfWork() as uow:
        await uow.runs.create(tenant_id, user_id=user, kind="chat", thread_id=thread)
        await uow.commit()
    ```
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Prepare a unit of work from an explicit session or a factory.

        ``factory`` defaults to the process-wide async session factory, which is
        resolved lazily so importing this module never opens a connection.
        """
        self._factory = factory
        self._session = session
        self._repositories: dict[str, Any] = {}
        self._owns_session = session is None

    @property
    def session(self) -> AsyncSession:
        """Return the session backing this unit of work, creating it on demand."""
        if self._session is None:
            resolved_factory = self._factory or get_session_factory()
            self._session = resolved_factory()
        return self._session

    @property
    def tenants(self) -> TenantRepository:
        """Return the tenant repository bound to this transaction."""
        return self._repository("tenants", TenantRepository)

    @property
    def users(self) -> UserRepository:
        """Return the user repository bound to this transaction."""
        return self._repository("users", UserRepository)

    @property
    def roles(self) -> RoleRepository:
        """Return the role repository bound to this transaction."""
        return self._repository("roles", RoleRepository)

    @property
    def grants(self) -> UserRoleGrantRepository:
        """Return the user-role-grant repository bound to this transaction."""
        return self._repository("grants", UserRoleGrantRepository)

    @property
    def runs(self) -> AgentRunRepository:
        """Return the agent-run repository bound to this transaction."""
        return self._repository("runs", AgentRunRepository)

    @property
    def run_events(self) -> RunEventRepository:
        """Return the run-event repository bound to this transaction."""
        return self._repository("run_events", RunEventRepository)

    @property
    def checkpoint_bindings(self) -> GraphCheckpointBindingRepository:
        """Return the checkpoint-binding repository bound to this transaction."""
        return self._repository("checkpoint_bindings", GraphCheckpointBindingRepository)

    @property
    def idempotency(self) -> IdempotencyRepository:
        """Return the idempotency repository bound to this transaction."""
        return self._repository("idempotency", IdempotencyRepository)

    @property
    def audit(self) -> AuditEventRepository:
        """Return the audit-event repository bound to this transaction."""
        return self._repository("audit", AuditEventRepository)

    @property
    def memories(self) -> MemoryItemRepository:
        """Return the memory-item repository bound to this transaction."""
        return self._repository("memories", MemoryItemRepository)

    @property
    def resource_catalog(self) -> UnitOfWorkResourceCatalog:
        """Return a resource catalogue bound to this same connection.

        Pass it to ``AuthorizationService`` for presence checks that observe the
        transaction's own uncommitted state.
        """
        return self._repository("resource_catalog", UnitOfWorkResourceCatalog)

    def _repository(self, name: str, factory: Any) -> Any:
        """Return the cached repository instance for this unit of work."""
        existing = self._repositories.get(name)
        if existing is None:
            existing = factory(self.session)
            self._repositories[name] = existing
        return existing

    async def set_tenant_context(self, tenant_id: str) -> None:
        """Publish the tenant to the connection for RLS defence in depth.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        await set_tenant_context(self.session, tenant_id)

    @asynccontextmanager
    async def tenant_scope(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        """Yield the session with the RLS tenant context applied.

        The application predicate in every repository is still applied: this only
        narrows what the database itself will return.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
        """
        await self.set_tenant_context(tenant_id)
        yield self.session

    async def flush(self) -> None:
        """Flush pending inserts so generated identifiers are available."""
        await self.session.flush()

    async def commit(self) -> None:
        """Commit this unit of work's transaction."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Roll back this unit of work's transaction, discarding all its work."""
        await self.session.rollback()

    async def close(self) -> None:
        """Release the session and its connection back to the pool."""
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._repositories.clear()

    async def __aenter__(self) -> UnitOfWork:
        """Open the unit of work without starting any SQL yet."""
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        """Roll back on error and always release the session.

        A unit of work is never committed implicitly: a caller that wants its
        work persisted calls :meth:`commit` explicitly, so an early ``return``
        cannot accidentally make a partial change durable.
        """
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self.close()
        return False


def unit_of_work(
    session: AsyncSession | None = None,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> UnitOfWork:
    """Build a unit of work over an explicit session or the app's factory."""
    return UnitOfWork(session, factory=factory)


def _as_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MemoryItemRepository:
    """Tenant-qualified memory reads and writes.

    Memory is not authoritative - it never overrides the evidence retrieved for
    the current turn - but it is still tenant-owned data, so every query is
    qualified by the owning tenant. The legacy service layer filtered by owner
    alone, which let two tenants that happen to share an owner id see each other's
    memories, and its inserts did not stamp ``tenant_id`` at all, which the
    enforced schema rejects outright.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the unit-of-work session."""
        self._session = session

    async def create(
        self,
        tenant_id: str,
        *,
        owner_type: str,
        owner_id: str,
        memory_type: str,
        content: str,
        source: str = "manual",
        confidence: float = 0.5,
        embedding: list[float] | None = None,
        meta_json: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem:
        """Store one memory item for this tenant.

        The caller flushes rather than commits: a unit of work owns the
        transaction, so the item becomes visible to the rest of the request
        without deciding its outcome.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when a required text field is empty.
        """
        tenant = require_tenant(tenant_id, "MemoryItemRepository.create")
        item = MemoryItem(
            tenant_id=tenant,
            owner_type=_require_text(owner_type, "owner_type"),
            owner_id=_require_text(owner_id, "owner_id"),
            memory_type=_require_text(memory_type, "memory_type"),
            content=_require_text(content, "content")[:2000],
            source=_require_text(source, "source"),
            confidence=max(0.0, min(float(confidence), 1.0)),
            embedding=embedding,
            meta_json=dict(meta_json or {}),
            expires_at=_as_utc(expires_at) if expires_at is not None else None,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def list_for_owner(
        self,
        tenant_id: str,
        owner_type: str,
        owner_id: str,
        *,
        memory_types: list[str] | None = None,
        now: datetime | None = None,
    ) -> list[MemoryItem]:
        """Return this tenant's live memories for one owner.

        Expired rows are filtered out here rather than by the caller, so an
        expired item cannot reach a prompt through a path that forgot to check.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when a required text field is empty.
        """
        tenant = require_tenant(tenant_id, "MemoryItemRepository.list_for_owner")
        moment = _as_utc(now) if now is not None else utc_now()
        statement = select(MemoryItem).where(
            _tenant_predicate(MemoryItem, tenant),
            MemoryItem.owner_type == _require_text(owner_type, "owner_type"),
            MemoryItem.owner_id == _require_text(owner_id, "owner_id"),
            # A null expiry means the item never expires, so the null case has to
            # be spelled out instead of collapsed into the comparison.
            (MemoryItem.expires_at.is_(None)) | (MemoryItem.expires_at > moment),
        )
        if memory_types is not None:
            allowed = [value for value in memory_types if value]
            if not allowed:
                return []
            statement = statement.where(MemoryItem.memory_type.in_(allowed))
        return await _fetch_all(
            self._session, statement.order_by(MemoryItem.updated_at.desc())
        )


    async def get_for_owner(
        self,
        tenant_id: str,
        memory_id: str,
        *,
        owner_type: str,
        owner_id: str,
    ) -> MemoryItem:
        """Return one memory of this tenant and owner, or a not-found error.

        The owner is part of the lookup rather than a check applied afterwards, so
        an id that belongs to another tenant or another owner is indistinguishable
        from one that does not exist. That keeps the refusal non-enumerating.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ValueError: when a required text field is empty.
            ResourceNotFoundError: when no matching memory exists.
        """
        tenant = require_tenant(tenant_id, "MemoryItemRepository.get_for_owner")
        identifier = _require_text(memory_id, "memory_id")
        rows = await _fetch_all(
            self._session,
            select(MemoryItem).where(
                _tenant_predicate(MemoryItem, tenant),
                MemoryItem.id == identifier,
                MemoryItem.owner_type == _require_text(owner_type, "owner_type"),
                MemoryItem.owner_id == _require_text(owner_id, "owner_id"),
            ),
        )
        if not rows:
            raise ResourceNotFoundError("memory", identifier)
        return rows[0]

    async def delete_for_owner(
        self,
        tenant_id: str,
        memory_id: str,
        *,
        owner_type: str,
        owner_id: str,
    ) -> None:
        """Remove one memory of this tenant and owner.

        The caller commits: a unit of work owns the transaction, so deleting does
        not quietly decide the outcome of the rest of the request.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when no matching memory exists.
        """
        item = await self.get_for_owner(
            tenant_id, memory_id, owner_type=owner_type, owner_id=owner_id
        )
        await self._session.delete(item)
        await self._session.flush()


class UnitOfWorkGrantSource:
    """Grant source that re-reads the caller's current grants inside the unit of work.

    This is what makes authorization *fresh* rather than token-scoped: the scopes
    come from the grants that are valid right now, so a revocation or an expiry
    takes effect on the next check instead of waiting for the token to be
    reissued. Reads go through the unit of work's own transaction, so the
    authorization decision sees the same snapshot as the effect it guards.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        """Bind the grant source to the unit of work it reads through."""
        self._uow = uow

    async def _selected(self, tenant_id: str, user_id: str, membership_id: str) -> list[Any]:
        """Return the currently valid grants for one membership.

        ``membership_id`` identifies a single grant row, so a caller holding a
        stale membership reference resolves to nothing rather than to whatever
        grants the user happens to have now.
        """
        member = _require_text(membership_id, "membership_id")
        grants = await self._uow.grants.active_grants(tenant_id, user_id)
        return [grant for grant in grants if getattr(grant, "id", None) == member]

    async def scopes_for(
        self, tenant_id: str, user_id: str, membership_id: str
    ) -> frozenset[str]:
        """Return the role actions and grant scope labels in force right now.

        Raises:
            TenantScopeError: when ``tenant_id`` is empty or missing.
            ResourceNotFoundError: when a referenced role row cannot be read.
        """
        scopes: set[str] = set()
        for grant in await self._selected(tenant_id, user_id, membership_id):
            label = getattr(grant, "scope", None)
            if isinstance(label, str) and label.strip():
                scopes.add(label.strip())
            role_id = getattr(grant, "role_id", None)
            if not isinstance(role_id, str) or not role_id.strip():
                continue
            role = await self._uow.roles.get(tenant_id, role_id)
            actions = getattr(role, "actions", None)
            if isinstance(actions, (list, tuple)):
                scopes.update(
                    action.strip()
                    for action in actions
                    if isinstance(action, str) and action.strip()
                )
        return frozenset(scopes)

    async def authorization_version_for(
        self, tenant_id: str, user_id: str, membership_id: str
    ) -> int:
        """Return a version that changes whenever any covering grant changes.

        The sum of the selected grants' versions is used rather than a single
        row's version, so adding or removing a grant also moves the value. The
        number itself is only ever carried into the audit trail; it is never the
        thing that decides an action.
        """
        total = 0
        for grant in await self._selected(tenant_id, user_id, membership_id):
            version = getattr(grant, "version", None)
            if isinstance(version, int) and not isinstance(version, bool):
                total += version
        return total
