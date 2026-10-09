"""T142 [US5] tenant-scoped administration of roles, quotas, runs and drift.

Every method here answers one of the User Story 5 questions *inside one tenant*:

* who holds which role, and may this operator hand it out or take it back;
* what quota is in force and what have we actually used;
* what work is queued, failed, or waiting for a human;
* what does one ``run_id`` look like from request to retry;
* which cross-store findings are open, and what did an operator decide.

The design rules are the same ones the rest of the enterprise surface follows:

1. **Tenant comes from the principal, never the body.** There is no
   ``tenant_id`` parameter on the public methods: the caller passes the request
   principal and the tenant is read off it. A foreign id therefore cannot select
   data.
2. **Allow-only, freshly re-read.** Every method calls
   ``AuthorizationService.authorize_fresh`` on its own unit of work, so a grant
   revoked a millisecond ago blocks the call. ``admin`` never implies
   ``cross_tenant_admin``: platform-wide capacity evidence needs the separate
   explicit grant.
3. **No privilege escalation.** An operator may only grant actions they
   currently hold, so a tenant admin cannot mint a ``cross_tenant_admin``
   membership for themselves.
4. **Unknown and foreign are the same 404.** Denials and misses never reveal
   whether another tenant owns an id.
5. **Mutations are audited in the same transaction.** A grant, revocation,
   recovery or reconciliation decision and its ``AuditEvent`` commit together;
   a denied attempt is also audited, and that audit is committed before the
   denial is raised so the refusal is not lost with the rolled-back work.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.auth.authorization import Action, AuthorizationService, ResourceRef
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import (
    AgentRun,
    CapacityTestRun,
    DurableJob,
    KnowledgeDocument,
    QuotaLease,
    QuotaPolicy,
    ReconciliationIssue,
    SubmissionJob,
    TaskWorkspace,
    UsageRecord,
    UserRoleGrant,
    utc_now,
)
from backend.app.db.repositories import (
    RESOURCE_KIND_TENANT,
    UnitOfWork,
    UnitOfWorkGrantSource,
)
from backend.app.observability.audit import AuditContentRejectedError, sanitize_audit_metadata

__all__ = [
    "AdminService",
    "PAGE_LIMIT_DEFAULT",
    "PAGE_LIMIT_MAX",
    "RECONCILIATION_ACTIONABLE_STATES",
]

PAGE_LIMIT_DEFAULT = 20
PAGE_LIMIT_MAX = 100

#: States a human may act on. ``resolved``/``repaired`` are terminal here.
RECONCILIATION_ACTIONABLE_STATES = frozenset({"open", "manual_required"})

#: Durable-job state that an operator may put back on the queue.
RECOVERABLE_JOB_STATE = "recoverable_failed"


def _not_found(message: str) -> ApplicationError:
    """Return the non-enumerable 404 used for absent and foreign resources."""
    return ApplicationError("RESOURCE_NOT_FOUND", message, status_code=404)


def _forbidden(message: str, **details: Any) -> ApplicationError:
    """Return a 403 carrying the stable authorization code."""
    return ApplicationError("AUTH_FORBIDDEN", message, status_code=403, details=details)


def _conflict(code: str, message: str, **details: Any) -> ApplicationError:
    """Return a 409 for an illegal state transition."""
    return ApplicationError(code, message, status_code=409, details=details or None)


def _page(limit: int, offset: int) -> tuple[int, int]:
    """Clamp a page request to the documented bounds.

    Out-of-range values are clamped rather than rejected: a paging parameter is
    not a security boundary, and failing a whole dashboard because a client sent
    ``limit=1000`` helps nobody. The response states the effective window, so the
    caller can always tell what it received.
    """
    bounded_limit = max(1, min(int(limit), PAGE_LIMIT_MAX))
    bounded_offset = max(0, int(offset))
    return bounded_limit, bounded_offset


def _iso(value: datetime | None) -> str | None:
    """Render an optional timestamp as ISO-8601."""
    return value.isoformat() if value is not None else None


def _safe_metadata(value: Any) -> tuple[dict[str, Any], list[str]]:
    """Redact one payload for the admin timeline.

    Reuses the audit module's own sanitizer, so the API cannot invent a second,
    weaker redaction policy. Payloads it rejects (malformed keys, binary bodies,
    oversized trees) are replaced with an empty object plus a ``rejected``
    marker rather than being echoed back raw.
    """
    if not isinstance(value, Mapping):
        return {}, []
    try:
        cleaned, redactions = sanitize_audit_metadata(dict(value))
    except AuditContentRejectedError as exc:
        return {}, [*list(exc.violations), "rejected"]
    return dict(cleaned), list(redactions)


class AdminService:
    """Tenant-scoped governance queries and audited mutations."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        page_limit_default: int = PAGE_LIMIT_DEFAULT,
    ) -> None:
        """Bind the service to an async session factory.

        A factory (not a request-scoped unit of work) is injected so the service
        owns the transaction it audits: the mutation and its audit row commit
        together, and a denial is still persisted before the error is raised.
        """
        self._factory = factory
        self._page_limit_default = page_limit_default

    # -- unit of work / authorization ----------------------------------------

    def _uow(self) -> UnitOfWork:
        """Open one unit of work over this service's factory."""
        return UnitOfWork(factory=self._factory)

    async def _authorize(
        self,
        uow: UnitOfWork,
        principal: Any,
        action: Action,
        *,
        resource_kind: str = RESOURCE_KIND_TENANT,
        resource_id: str | None = None,
    ) -> Any:
        """Authorize freshly against the current grants of this membership."""
        service = AuthorizationService(
            grant_source=UnitOfWorkGrantSource(uow),
            resource_catalog=uow.resource_catalog,
        )
        ref = ResourceRef(
            kind=resource_kind,
            id=resource_id or principal.tenant_id,
            tenant_id=principal.tenant_id,
        )
        return await service.authorize_fresh(principal, action, ref)

    async def _audit(
        self,
        uow: UnitOfWork,
        principal: Any,
        *,
        action: str,
        outcome: str,
        resource_kind: str,
        resource_id: str | None = None,
        run_id: str | None = None,
        reason_code: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one audit row on the caller's unit of work."""
        await uow.audit.append(
            principal.tenant_id,
            event_id=str(uuid4()),
            actor_ref=principal.user_id,
            action=action,
            outcome=outcome,
            resource_kind=resource_kind,
            resource_id=resource_id,
            run_id=run_id,
            request_id=getattr(principal, "request_id", None),
            trace_id=None,
            authorization_version=int(
                getattr(principal, "authorization_version", 1) or 1
            ),
            reason_code=reason_code,
            redacted_metadata=dict(metadata or {}),
        )

    async def _authorize_or_audit_denial(
        self,
        uow: UnitOfWork,
        principal: Any,
        action: Action,
        *,
        audit_action: str,
        resource_kind: str = RESOURCE_KIND_TENANT,
        resource_id: str | None = None,
    ) -> None:
        """Authorize; on denial persist the refusal, then raise the 403.

        The denial audit is committed on its own unit of work. If it shared the
        request's transaction it would be rolled back by the very error that the
        audit exists to record.
        """
        decision = await self._authorize(
            uow, principal, action, resource_kind=resource_kind, resource_id=resource_id
        )
        if decision.allowed:
            return
        await self._audit(
            uow,
            principal,
            action=audit_action,
            outcome="denied",
            resource_kind=resource_kind,
            resource_id=resource_id,
            reason_code=decision.code,
            metadata={"required_action": str(action)},
        )
        await uow.commit()
        decision.raise_for_denial()

    # -- roles and grants ----------------------------------------------------

    async def list_roles(self, principal: Any) -> dict[str, Any]:
        """Return the tenant's roles, their actions and active grant counts."""
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.role.list"
            )
            roles = await uow.roles.list_for_tenant(principal.tenant_id)

            counts: dict[str, int] = {}
            rows = await uow.session.execute(
                select(UserRoleGrant.role_id, func.count(UserRoleGrant.id))
                .where(
                    UserRoleGrant.tenant_id == principal.tenant_id,
                    UserRoleGrant.revoked_at.is_(None),
                )
                .group_by(UserRoleGrant.role_id)
            )
            for role_id, total in rows.all():
                counts[str(role_id)] = int(total)

            items = [
                {
                    "role_id": role.id,
                    "code": role.code,
                    "name": role.name,
                    "description": role.description,
                    "actions": list(role.actions or []),
                    "active_grants": counts.get(role.id, 0),
                    "version": role.version,
                }
                for role in sorted(roles, key=lambda item: item.code)
            ]
            await self._audit(
                uow,
                principal,
                action="admin.role.list",
                outcome="allowed",
                resource_kind="role",
                metadata={"returned": len(items)},
            )
            await uow.commit()
            return {"items": items, "total": len(items)}

    async def list_grants(
        self,
        principal: Any,
        *,
        limit: int = PAGE_LIMIT_DEFAULT,
        offset: int = 0,
        member_id: str | None = None,
    ) -> dict[str, Any]:
        """Return granted memberships, newest first, with their validity window.

        The filter is called ``member_id`` rather than ``user_id`` on purpose:
        the platform reserves a request-supplied ``user_id``/``tenant_id`` for
        *identity claims* and rejects one that does not match the token. A
        dashboard filter is not a claim, so it must not borrow that name.
        """
        bounded_limit, bounded_offset = _page(limit, offset)
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.grant.list"
            )
            predicate = [UserRoleGrant.tenant_id == principal.tenant_id]
            if member_id:
                predicate.append(UserRoleGrant.user_id == member_id)
            total = int(
                (
                    await uow.session.execute(
                        select(func.count(UserRoleGrant.id)).where(*predicate)
                    )
                ).scalar_one()
            )
            rows = (
                await uow.session.execute(
                    select(UserRoleGrant)
                    .where(*predicate)
                    .order_by(UserRoleGrant.created_at.desc(), UserRoleGrant.id)
                    .limit(bounded_limit)
                    .offset(bounded_offset)
                )
            ).scalars().all()
            items = [self._grant_view(row) for row in rows]
            await self._audit(
                uow,
                principal,
                action="admin.grant.list",
                outcome="allowed",
                resource_kind="user_role_grant",
                metadata={"returned": len(items), "offset": bounded_offset},
            )
            await uow.commit()
            return {
                "items": items,
                "total": total,
                "limit": bounded_limit,
                "offset": bounded_offset,
            }

    async def create_grant(
        self,
        principal: Any,
        *,
        user_id: str,
        role_id: str | None = None,
        role_code: str | None = None,
        valid_from: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Grant one role to one member of the caller's tenant.

        Repeating an identical active grant returns the existing row
        (``created: false``) instead of stacking duplicates. Granting a scope the
        operator does not hold is refused, which is what keeps ``admin`` from
        escalating into ``cross_tenant_admin``.
        """
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.grant.create"
            )

            role = None
            if role_id:
                role = await uow.roles.get(principal.tenant_id, role_id)
            elif role_code:
                role = await uow.roles.get_by_code(principal.tenant_id, role_code)
            if role is None:
                await self._audit(
                    uow,
                    principal,
                    action="admin.grant.create",
                    outcome="denied",
                    resource_kind="role",
                    reason_code="RESOURCE_NOT_FOUND",
                    metadata={"reason": "role_not_in_tenant"},
                )
                await uow.commit()
                raise _not_found("no such role in this tenant")

            member = await uow.users.get(principal.tenant_id, user_id)
            if member is None:
                await self._audit(
                    uow,
                    principal,
                    action="admin.grant.create",
                    outcome="denied",
                    resource_kind="user",
                    reason_code="RESOURCE_NOT_FOUND",
                    metadata={"reason": "member_not_in_tenant"},
                )
                await uow.commit()
                raise _not_found("no such member in this tenant")

            missing = sorted(set(role.actions or []) - set(principal.effective_scopes()))
            if missing:
                await self._audit(
                    uow,
                    principal,
                    action="admin.grant.create",
                    outcome="denied",
                    resource_kind="role",
                    resource_id=role.id,
                    reason_code="AUTH_FORBIDDEN",
                    metadata={"reason": "grant_exceeds_operator_scopes",
                              "missing_scopes": missing},
                )
                await uow.commit()
                raise _forbidden(
                    "cannot grant actions the operator does not hold",
                    missing_scopes=missing,
                )

            for existing in await uow.grants.active_grants(
                principal.tenant_id, user_id
            ):
                if existing.role_id == role.id:
                    await uow.commit()
                    return {"grant": self._grant_view(existing), "created": False}

            grant = await uow.grants.create(
                principal.tenant_id,
                user_id=user_id,
                role_id=role.id,
                scope="tenant",
                valid_from=valid_from,
                expires_at=expires_at,
                granted_by=principal.user_id,
            )
            await self._audit(
                uow,
                principal,
                action="admin.grant.create",
                outcome="allowed",
                resource_kind="user_role_grant",
                resource_id=grant.id,
                metadata={
                    "role_code": role.code,
                    "target_user_id": user_id,
                    "expires_at": _iso(grant.expires_at),
                },
            )
            await uow.commit()
            return {"grant": self._grant_view(grant), "created": True}

    async def revoke_grant(
        self,
        principal: Any,
        *,
        grant_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Revoke one grant. A second revoke is an idempotent no-op."""
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.grant.revoke"
            )
            grant = await uow.grants.get(principal.tenant_id, grant_id)
            if grant.revoked_at is not None:
                await uow.commit()
                return {"grant": self._grant_view(grant), "revoked": False}
            revoked = await uow.grants.revoke(
                principal.tenant_id,
                grant.id,
                revoked_by=principal.user_id,
                reason=reason,
                expected_version=grant.version,
            )
            await self._audit(
                uow,
                principal,
                action="admin.grant.revoke",
                outcome="allowed",
                resource_kind="user_role_grant",
                resource_id=grant.id,
                metadata={"reason": reason or ""},
            )
            await uow.commit()
            return {"grant": self._grant_view(revoked), "revoked": True}

    # -- quota / usage / health ---------------------------------------------

    async def quota_summary(self, principal: Any) -> dict[str, Any]:
        """Return the effective quota policies, usage totals and live leases."""
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.quota.read"
            )

            policies = (
                await uow.session.execute(
                    select(QuotaPolicy)
                    .where(
                        QuotaPolicy.active.is_(True),
                        or_(
                            QuotaPolicy.tenant_id.is_(None),
                            QuotaPolicy.tenant_id == principal.tenant_id,
                        ),
                    )
                    .order_by(
                        QuotaPolicy.scope,
                        QuotaPolicy.workload,
                        QuotaPolicy.version.desc(),
                    )
                )
            ).scalars().all()
            effective: dict[tuple[str, str], QuotaPolicy] = {}
            for policy in policies:
                key = (policy.scope, policy.workload or "")
                if key not in effective:
                    effective[key] = policy

            usage_rows = (
                await uow.session.execute(
                    select(
                        UsageRecord.kind,
                        func.count(UsageRecord.id),
                        func.coalesce(func.sum(UsageRecord.requests), 0),
                        func.coalesce(func.sum(UsageRecord.reserved_tokens), 0),
                        func.coalesce(func.sum(UsageRecord.actual_tokens), 0),
                    )
                    .where(UsageRecord.tenant_id == principal.tenant_id)
                    .group_by(UsageRecord.kind)
                )
            ).all()
            usage = [
                {
                    "kind": str(kind),
                    "records": int(records),
                    "requests": int(requests),
                    "reserved_tokens": int(reserved),
                    "actual_tokens": int(actual),
                }
                for kind, records, requests, reserved, actual in usage_rows
            ]
            now = utc_now()
            live_leases = int(
                (
                    await uow.session.execute(
                        select(func.count(QuotaLease.id)).where(
                            QuotaLease.tenant_id == principal.tenant_id,
                            QuotaLease.released_at.is_(None),
                            QuotaLease.expires_at > now,
                        )
                    )
                ).scalar_one()
            )
            await self._audit(
                uow,
                principal,
                action="admin.quota.read",
                outcome="allowed",
                resource_kind=RESOURCE_KIND_TENANT,
                metadata={"policies": len(effective), "usage_kinds": len(usage)},
            )
            await uow.commit()
            return {
                "policies": [self._policy_view(item) for item in effective.values()],
                "usage": usage,
                "live_leases": live_leases,
            }

    async def health(self, principal: Any) -> dict[str, Any]:
        """Return tenant-scoped queue, indexing, submission and drift health."""
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.health.read"
            )
            tenant_id = principal.tenant_id

            async def grouped(model: Any, column: Any) -> list[dict[str, Any]]:
                rows = (
                    await uow.session.execute(
                        select(column, func.count(model.id))
                        .where(model.tenant_id == tenant_id)
                        .group_by(column)
                    )
                ).all()
                return [
                    {"value": str(value), "count": int(count)} for value, count in rows
                ]

            jobs = await grouped(DurableJob, DurableJob.state)
            documents = await grouped(KnowledgeDocument, KnowledgeDocument.index_status)
            submissions = await grouped(SubmissionJob, SubmissionJob.state)
            workspaces = await grouped(TaskWorkspace, TaskWorkspace.status)

            oldest_queued = (
                await uow.session.execute(
                    select(func.min(DurableJob.available_at)).where(
                        DurableJob.tenant_id == tenant_id,
                        DurableJob.state == "queued",
                    )
                )
            ).scalar_one_or_none()

            open_issues = (
                await uow.session.execute(
                    select(
                        ReconciliationIssue.issue_kind,
                        ReconciliationIssue.severity,
                        func.count(ReconciliationIssue.id),
                    )
                    .where(
                        ReconciliationIssue.tenant_id == tenant_id,
                        ReconciliationIssue.state.in_(
                            sorted(RECONCILIATION_ACTIONABLE_STATES)
                        ),
                    )
                    .group_by(ReconciliationIssue.issue_kind, ReconciliationIssue.severity)
                )
            ).all()

            from backend.app.db.models import Material, VectorManifest

            retrievable = int(
                (
                    await uow.session.execute(
                        select(func.count(VectorManifest.id)).where(
                            VectorManifest.tenant_id == tenant_id,
                            VectorManifest.retrievable.is_(True),
                        )
                    )
                ).scalar_one()
            )
            manifest_drift = int(
                (
                    await uow.session.execute(
                        select(func.count(VectorManifest.id)).where(
                            VectorManifest.tenant_id == tenant_id,
                            VectorManifest.retrievable.is_(True),
                            VectorManifest.indexed_count
                            != VectorManifest.expected_count,
                        )
                    )
                ).scalar_one()
            )
            active_materials = int(
                (
                    await uow.session.execute(
                        select(func.count(Material.id)).where(
                            Material.tenant_id == tenant_id,
                            Material.active_version_id.is_not(None),
                            Material.status == "available",
                        )
                    )
                ).scalar_one()
            )
            await self._audit(
                uow,
                principal,
                action="admin.health.read",
                outcome="allowed",
                resource_kind=RESOURCE_KIND_TENANT,
                metadata={"job_states": len(jobs), "open_issues": len(open_issues)},
            )
            await uow.commit()
            return {
                "jobs": jobs,
                "oldest_queued_at": _iso(oldest_queued),
                "documents": documents,
                "submissions": submissions,
                "workspaces": workspaces,
                "retrieval": {
                    "retrievable_manifests": retrievable,
                    "manifests_with_count_drift": manifest_drift,
                    "active_materials": active_materials,
                },
                "reconciliation": {
                    "open_issues": [
                        {
                            "issue_kind": str(kind),
                            "severity": str(severity),
                            "count": int(count),
                        }
                        for kind, severity, count in open_issues
                    ]
                },
            }

    # -- runs / audit timeline / recovery -----------------------------------

    async def list_runs(
        self,
        principal: Any,
        *,
        limit: int = PAGE_LIMIT_DEFAULT,
        offset: int = 0,
        status: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Return the tenant's runs, newest first, with optional filters."""
        bounded_limit, bounded_offset = _page(limit, offset)
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.run.list"
            )
            predicate = [AgentRun.tenant_id == principal.tenant_id]
            if status:
                predicate.append(AgentRun.status == status)
            if kind:
                predicate.append(AgentRun.kind == kind)
            total = int(
                (
                    await uow.session.execute(
                        select(func.count(AgentRun.id)).where(*predicate)
                    )
                ).scalar_one()
            )
            runs = (
                await uow.session.execute(
                    select(AgentRun)
                    .where(*predicate)
                    .order_by(AgentRun.created_at.desc(), AgentRun.id)
                    .limit(bounded_limit)
                    .offset(bounded_offset)
                )
            ).scalars().all()
            await self._audit(
                uow,
                principal,
                action="admin.run.list",
                outcome="allowed",
                resource_kind="agent_run",
                metadata={"returned": len(runs), "offset": bounded_offset},
            )
            await uow.commit()
            return {
                "items": [self._run_view(run) for run in runs],
                "total": total,
                "limit": bounded_limit,
                "offset": bounded_offset,
            }

    async def run_audit(
        self,
        principal: Any,
        *,
        run_id: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return one run's correlated, redacted timeline.

        Audit rows (authorization decisions, approvals, submissions) and durable
        run events (stage milestones, evidence gate) are merged by time, so the
        operator sees request → evidence → tool → approval → submission → retry in
        one list. Unknown and foreign runs are the same 404.
        """
        bounded_limit = max(1, min(int(limit), 500))
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.run.audit.read"
            )
            run = await uow.runs.find(principal.tenant_id, run_id)
            if run is None:
                await uow.commit()
                raise _not_found("no such run in this tenant")

            timeline: list[dict[str, Any]] = []
            for event in await uow.audit.list_for_run(
                principal.tenant_id, run_id, limit=bounded_limit
            ):
                metadata, redactions = _safe_metadata(event.redacted_metadata)
                timeline.append(
                    {
                        "source": "audit",
                        "event_id": event.event_id,
                        "action": event.action,
                        "outcome": event.outcome,
                        "reason_code": event.reason_code,
                        "actor_ref": event.actor_ref,
                        "resource_kind": event.resource_kind,
                        "resource_id": event.resource_id,
                        "authorization_version": event.authorization_version,
                        "occurred_at": _iso(event.occurred_at),
                        "metadata": metadata,
                        "redactions": redactions,
                    }
                )
            for event in await uow.run_events.list_for_run(
                principal.tenant_id, run_id, limit=bounded_limit
            ):
                payload, redactions = _safe_metadata(event.payload)
                timeline.append(
                    {
                        "source": "run_event",
                        "event_id": event.event_id,
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "stage": event.stage,
                        "public_status": event.public_status,
                        "occurred_at": _iso(event.occurred_at),
                        "metadata": payload,
                        "redactions": redactions,
                    }
                )
            timeline.sort(key=lambda item: (item["occurred_at"] or "", item["event_id"]))
            await self._audit(
                uow,
                principal,
                action="admin.run.audit.read",
                outcome="allowed",
                resource_kind="agent_run",
                resource_id=run.run_id,
                run_id=run.run_id,
                metadata={"entries": len(timeline)},
            )
            await uow.commit()
            return {
                "run": self._run_view(run),
                "items": timeline,
                "total": len(timeline),
            }

    async def recover_run(
        self,
        principal: Any,
        *,
        run_id: str,
        extend_attempts: bool = False,
    ) -> dict[str, Any]:
        """Put a recoverable-failed durable job back on the queue.

        Only ``recoverable_failed`` may be requeued. A ``terminal_failed`` job is
        *not* resurrected: per the data model a terminal run is recovered by
        starting a new run, and pretending otherwise would erase the boundary
        between "retry" and "new work". When the attempt budget is spent the
        operator must opt into ``extend_attempts``, which is recorded in the
        audit trail as an explicit decision rather than silently resetting it.
        """
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.run.recover"
            )
            job = (
                await uow.session.execute(
                    select(DurableJob).where(
                        DurableJob.tenant_id == principal.tenant_id,
                        or_(DurableJob.id == run_id, DurableJob.run_id == run_id),
                    )
                )
            ).scalars().first()
            if job is None:
                await uow.commit()
                raise _not_found("no such run in this tenant")
            # Capture the pre-update values now: an ORM-enabled UPDATE
            # synchronizes the loaded object in place, so reading ``job.state``
            # after the write would report the new state as the previous one.
            previous_state = job.state
            previous_attempts = job.attempts
            previous_max_attempts = job.max_attempts
            if previous_state != RECOVERABLE_JOB_STATE:
                await self._audit(
                    uow,
                    principal,
                    action="admin.run.recover",
                    outcome="denied",
                    resource_kind="durable_job",
                    resource_id=job.id,
                    run_id=job.run_id,
                    reason_code="RUN_NOT_RECOVERABLE",
                    metadata={"state": previous_state},
                )
                await uow.commit()
                raise _conflict(
                    "RUN_NOT_RECOVERABLE",
                    "only a recoverable_failed run may be requeued",
                    state=previous_state,
                )
            if previous_attempts >= previous_max_attempts and not extend_attempts:
                await self._audit(
                    uow,
                    principal,
                    action="admin.run.recover",
                    outcome="denied",
                    resource_kind="durable_job",
                    resource_id=job.id,
                    run_id=job.run_id,
                    reason_code="RUN_ATTEMPTS_EXHAUSTED",
                    metadata={
                        "attempts": previous_attempts,
                        "max_attempts": previous_max_attempts,
                    },
                )
                await uow.commit()
                raise _conflict(
                    "RUN_ATTEMPTS_EXHAUSTED",
                    "attempt budget is spent; pass extend_attempts to authorize one more",
                    attempts=previous_attempts,
                    max_attempts=previous_max_attempts,
                )

            values: dict[str, Any] = {
                "state": "queued",
                "lease_owner": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "available_at": utc_now(),
                "version": job.version + 1,
            }
            extended = bool(
                extend_attempts and previous_attempts >= previous_max_attempts
            )
            if extended:
                values["max_attempts"] = previous_attempts + 1
            result = await uow.session.execute(
                update(DurableJob)
                .where(
                    DurableJob.id == job.id,
                    DurableJob.tenant_id == principal.tenant_id,
                    DurableJob.state == RECOVERABLE_JOB_STATE,
                    DurableJob.version == job.version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                await uow.rollback()
                raise _conflict(
                    "VERSION_CONFLICT", "the run changed while it was being recovered"
                )
            await self._audit(
                uow,
                principal,
                action="admin.run.recover",
                outcome="allowed",
                resource_kind="durable_job",
                resource_id=job.id,
                run_id=job.run_id,
                metadata={
                    "previous_state": previous_state,
                    "attempts": previous_attempts,
                    "extended": extended,
                },
            )
            await uow.commit()
            refreshed = await uow.session.get(DurableJob, job.id)
            return {
                "run_id": refreshed.run_id if refreshed else job.run_id,
                "job_id": job.id,
                "previous_state": previous_state,
                "state": refreshed.state if refreshed else "queued",
                "attempts": refreshed.attempts if refreshed else previous_attempts,
                "max_attempts": (
                    refreshed.max_attempts if refreshed else previous_max_attempts
                ),
                "version": refreshed.version if refreshed else job.version + 1,
            }

    # -- reconciliation ------------------------------------------------------

    async def list_reconciliation(
        self,
        principal: Any,
        *,
        limit: int = PAGE_LIMIT_DEFAULT,
        offset: int = 0,
        state: str | None = None,
        issue_kind: str | None = None,
    ) -> dict[str, Any]:
        """Return cross-store findings, newest first.

        Only fingerprints and identifiers leave this method: the object key,
        bucket and host path never enter the row in the first place, so there is
        nothing to strip here.
        """
        bounded_limit, bounded_offset = _page(limit, offset)
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.reconciliation.list"
            )
            predicate = [ReconciliationIssue.tenant_id == principal.tenant_id]
            if state:
                predicate.append(ReconciliationIssue.state == state)
            if issue_kind:
                predicate.append(ReconciliationIssue.issue_kind == issue_kind)
            total = int(
                (
                    await uow.session.execute(
                        select(func.count(ReconciliationIssue.id)).where(*predicate)
                    )
                ).scalar_one()
            )
            issues = (
                await uow.session.execute(
                    select(ReconciliationIssue)
                    .where(*predicate)
                    .order_by(ReconciliationIssue.detected_at.desc(), ReconciliationIssue.id)
                    .limit(bounded_limit)
                    .offset(bounded_offset)
                )
            ).scalars().all()
            await self._audit(
                uow,
                principal,
                action="admin.reconciliation.list",
                outcome="allowed",
                resource_kind="reconciliation_issue",
                metadata={"returned": len(issues), "offset": bounded_offset},
            )
            await uow.commit()
            return {
                "items": [self._issue_view(issue) for issue in issues],
                "total": total,
                "limit": bounded_limit,
                "offset": bounded_offset,
            }

    async def retry_reconciliation(
        self, principal: Any, *, issue_id: str
    ) -> dict[str, Any]:
        """Reopen a human-escalated finding with a fresh attempt budget."""
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.reconciliation.retry"
            )
            issue = await self._issue_for_tenant(uow, principal, issue_id)
            # An ORM-enabled UPDATE synchronizes the loaded row in place; capture
            # the pre-update state before executing so the response and audit do
            # not report the new state as the previous one.
            previous_state = issue.state
            if previous_state not in RECONCILIATION_ACTIONABLE_STATES:
                await uow.commit()
                raise _conflict(
                    "RECONCILIATION_ISSUE_NOT_ACTIONABLE",
                    "a resolved or repaired issue cannot be retried",
                    state=previous_state,
                )
            result = await uow.session.execute(
                update(ReconciliationIssue)
                .where(
                    ReconciliationIssue.id == issue.id,
                    ReconciliationIssue.tenant_id == principal.tenant_id,
                    ReconciliationIssue.state == issue.state,
                    ReconciliationIssue.version == issue.version,
                )
                .values(
                    state="open",
                    attempts=0,
                    next_attempt_at=utc_now(),
                    last_error_code=None,
                    version=issue.version + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                await uow.rollback()
                raise _conflict(
                    "VERSION_CONFLICT", "the issue changed while it was being retried"
                )
            await self._audit(
                uow,
                principal,
                action="admin.reconciliation.retry",
                outcome="allowed",
                resource_kind="reconciliation_issue",
                resource_id=issue.id,
                metadata={"previous_state": previous_state, "issue_kind": issue.issue_kind},
            )
            await uow.commit()
            return {"issue_id": issue.id, "state": "open", "previous_state": previous_state}

    async def resolve_reconciliation(
        self,
        principal: Any,
        *,
        issue_id: str,
        resolution: str,
    ) -> dict[str, Any]:
        """Close a finding with an operator-supplied resolution note."""
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow, principal, Action.ADMIN, audit_action="admin.reconciliation.resolve"
            )
            issue = await self._issue_for_tenant(uow, principal, issue_id)
            previous_state = issue.state
            if previous_state not in RECONCILIATION_ACTIONABLE_STATES:
                await uow.commit()
                raise _conflict(
                    "RECONCILIATION_ISSUE_NOT_ACTIONABLE",
                    "only an open or manual_required issue can be resolved",
                    state=previous_state,
                )
            result = await uow.session.execute(
                update(ReconciliationIssue)
                .where(
                    ReconciliationIssue.id == issue.id,
                    ReconciliationIssue.tenant_id == principal.tenant_id,
                    ReconciliationIssue.state == issue.state,
                    ReconciliationIssue.version == issue.version,
                )
                .values(
                    state="resolved",
                    resolution=resolution,
                    resolved_at=utc_now(),
                    updated_at=utc_now(),
                    version=issue.version + 1,
                )
            )
            if result.rowcount != 1:
                await uow.rollback()
                raise _conflict(
                    "VERSION_CONFLICT", "the issue changed while it was being resolved"
                )
            await self._audit(
                uow,
                principal,
                action="admin.reconciliation.resolve",
                outcome="allowed",
                resource_kind="reconciliation_issue",
                resource_id=issue.id,
                metadata={
                    "previous_state": previous_state,
                    "issue_kind": issue.issue_kind,
                    "resolution": resolution,
                },
            )
            await uow.commit()
            return {
                "issue_id": issue.id,
                "state": "resolved",
                "previous_state": previous_state,
                "resolution": resolution,
            }

    async def _issue_for_tenant(
        self, uow: UnitOfWork, principal: Any, issue_id: str
    ) -> ReconciliationIssue:
        """Load one finding of this tenant, or raise the shared 404."""
        issue = (
            await uow.session.execute(
                select(ReconciliationIssue).where(
                    ReconciliationIssue.tenant_id == principal.tenant_id,
                    ReconciliationIssue.id == issue_id,
                )
            )
        ).scalars().first()
        if issue is None:
            raise _not_found("no such reconciliation issue in this tenant")
        return issue

    # -- platform capacity evidence -----------------------------------------

    async def list_capacity(
        self,
        principal: Any,
        *,
        limit: int = PAGE_LIMIT_DEFAULT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return platform capacity runs. Requires the explicit cross-tenant grant.

        ``CapacityTestRun`` measures the platform, not a customer, so it is not
        tenant-scoped. A tenant admin without ``cross_tenant_admin`` therefore
        gets the same 403 as any other missing scope -- no partial, redacted view
        that would still confirm other tenants' load tests exist.
        """
        bounded_limit, bounded_offset = _page(limit, offset)
        async with self._uow() as uow:
            await uow.set_tenant_context(principal.tenant_id)
            await self._authorize_or_audit_denial(
                uow,
                principal,
                Action.CROSS_TENANT_ADMIN,
                audit_action="admin.capacity.list",
            )
            total = int(
                (
                    await uow.session.execute(select(func.count(CapacityTestRun.id)))
                ).scalar_one()
            )
            runs = (
                await uow.session.execute(
                    select(CapacityTestRun)
                    .order_by(CapacityTestRun.started_at.desc(), CapacityTestRun.id)
                    .limit(bounded_limit)
                    .offset(bounded_offset)
                )
            ).scalars().all()
            await self._audit(
                uow,
                principal,
                action="admin.capacity.list",
                outcome="allowed",
                resource_kind="capacity_test_run",
                metadata={"returned": len(runs)},
            )
            await uow.commit()
            return {
                "items": [self._capacity_view(run) for run in runs],
                "total": total,
                "limit": bounded_limit,
                "offset": bounded_offset,
            }

    # -- serializers ---------------------------------------------------------

    @staticmethod
    def _grant_view(grant: UserRoleGrant) -> dict[str, Any]:
        """Serialize a grant without its tenant's internal identity."""
        return {
            "grant_id": grant.id,
            "user_id": grant.user_id,
            "role_id": grant.role_id,
            "scope": grant.scope,
            "valid_from": _iso(grant.valid_from),
            "expires_at": _iso(grant.expires_at),
            "revoked_at": _iso(grant.revoked_at),
            "revocation_reason": grant.revocation_reason,
            "version": grant.version,
        }

    @staticmethod
    def _run_view(run: AgentRun) -> dict[str, Any]:
        """Serialize an agent run for the governance list."""
        return {
            "run_id": run.run_id,
            "kind": run.kind,
            "status": run.status,
            "evidence_gate": run.evidence_gate,
            "user_id": run.user_id,
            "conversation_id": run.conversation_id,
            "tool_call_count": run.tool_call_count,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "version": run.version,
        }

    @staticmethod
    def _policy_view(policy: QuotaPolicy) -> dict[str, Any]:
        """Serialize one effective quota policy."""
        return {
            "policy_id": policy.id,
            "scope": policy.scope,
            "tenant_id": policy.tenant_id,
            "workload": policy.workload,
            "requests_per_window": policy.requests_per_window,
            "window_seconds": policy.window_seconds,
            "tokens_per_window": policy.tokens_per_window,
            "max_concurrency": policy.max_concurrency,
            "queue_admission_limit": policy.queue_admission_limit,
            "version": policy.version,
        }

    @staticmethod
    def _issue_view(issue: ReconciliationIssue) -> dict[str, Any]:
        """Serialize one finding; fingerprints only, never storage keys."""
        return {
            "issue_id": issue.id,
            "store_pair": issue.store_pair,
            "issue_kind": issue.issue_kind,
            "resource_kind": issue.resource_kind,
            "resource_id": issue.resource_id,
            "version_id": issue.version_id,
            "observed_fingerprint": issue.observed_fingerprint,
            "expected_fingerprint": issue.expected_fingerprint,
            "severity": issue.severity,
            "state": issue.state,
            "attempts": issue.attempts,
            "max_attempts": issue.max_attempts,
            "next_attempt_at": _iso(issue.next_attempt_at),
            "last_error_code": issue.last_error_code,
            "resolution": issue.resolution,
            "detected_at": _iso(issue.detected_at),
            "resolved_at": _iso(issue.resolved_at),
            "version": issue.version,
        }

    @staticmethod
    def _capacity_view(run: CapacityTestRun) -> dict[str, Any]:
        """Serialize a capacity run, keeping mock and real-provider runs apart."""
        return {
            "capacity_run_id": run.id,
            "commit_sha": run.commit_sha,
            "scenario": run.scenario,
            "suite_version": run.suite_version,
            "llm_mode": run.llm_mode,
            "started_at": _iso(run.started_at),
            "duration_seconds": run.duration_seconds,
            "raw_artifact_uri": run.raw_artifact_uri,
            "raw_artifact_sha256": run.raw_artifact_sha256,
            "summary_metrics": dict(run.summary_metrics or {}),
            "verdict": run.verdict,
            "known_limits": run.known_limits,
        }
