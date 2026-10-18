"""T151 contract tests for ``contracts/internal-contracts.md``.

Every assertion here is a *documented invariant*, not an implementation detail:
identity is membership-derived and immutable, authorization is allow-only and
deny-by-default, the graph/job lifecycles are closed and version-guarded, the
object store derives its own keys, the sandbox refuses unallowlisted processors,
audit appends are idempotent by ``event_id``, SSE frames carry ordered ids, and
every documented error code exists in the one stable vocabulary with exactly the
documented envelope.

Deliberately out of scope (see the task report): a live MinIO put/get round-trip
(asserted in the gated storage suite), a live Redis SSE stream (asserted against
a real Redis in ``test_sse_resume.py``), and the state-machine breadth already
pinned by ``test_job_state_machine.py`` -- only the cross-cutting contract
invariants are restated here.

No infrastructure is used: SQLite with an in-process session factory, in-memory
fakes, and pure value objects.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel
from starlette.requests import Request

from backend.app.api.routes_runs import RunRequest
from backend.app.auth.authorization import (
    Action,
    AuthorizationService,
    ResourceRef,
    UnknownActionError,
)
from backend.app.auth.principal import RequestPrincipal
from backend.app.core.exceptions import ApplicationError, _error_payload
from backend.app.db.models import Tenant
from backend.app.db.repositories import (
    RunEventRepository,
    RunEventSequenceConflictError,
)
from backend.app.db.session import build_async_engine
from backend.app.graph.entrypoints import (
    ENTRYPOINT_REQUIRED_SCOPE,
    GraphEntrypoint,
)
from backend.app.graph.state import (
    SCHEMA_VERSION,
    AgentRunState,
    GraphStatus,
    NodeName,
    RetryBudget,
    ToolBudget,
)
from backend.app.integrations.mock_submission import (
    MOCK_STATUS,
    MockReimbursementConnector,
)
from backend.app.jobs.service import JobService, JobStateError
from backend.app.observability.audit import (
    AuditRecord,
    AuditSink,
    GuardedAuditSink,
    InMemoryAuditPersistence,
    new_event_id,
)
from backend.app.observability.errors import ERROR_SEMANTICS, ErrorCode
from backend.app.retrieval.indexer import VectorIndexer
from backend.app.retrieval.milvus import (
    MANDATORY_PRE_ANN_FILTER_FIELDS,
    RetrievalScope,
)
from backend.app.sandbox.processors import (
    ProcessorError,
    default_registry,
)
from backend.app.sandbox.runner import (
    LocalSandboxBackend,
    SandboxInput,
    SandboxRequest,
    SandboxRunner,
)
from backend.app.sandbox.validation import WorkspaceEscape
from backend.app.sse.channel import BoundedEventChannel, ChannelClosed, ChannelEvent
from backend.app.sse.endpoint import _frame
from backend.app.sse.run_events import (
    JOB_STATE_TO_RUN_EVENT,
    RUN_FAILED,
    RUN_SUCCEEDED,
    is_terminal_event,
)
from backend.app.sse.snapshot import DurableRunSnapshot
from backend.app.storage.object_store import (
    CrossTenantObjectAccess,
    ObjectStore,
    ObjectStoreConfig,
    UploadGrant,
    UploadVerificationError,
    derive_object_key,
)

TENANT = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
MEMBERSHIP = "55555555-5555-4555-8555-555555555555"
RUN_ID = "88888888-8888-4888-8888-888888888888"

#: The exact node sequence from the contract's "Node contract" table.
CONTRACT_NODES = {
    "validate",
    "memory_load",
    "rewrite",
    "retrieve",
    "rerank",
    "evidence_gate",
    "plan_or_tool",
    "sandbox",
    "approval_interrupt",
    "generate",
    "writeback",
    "finalize",
}

#: The minimum stable-code vocabulary from the contract's "Error Contract".
CONTRACT_ERROR_CODES = {
    "AUTH_FORBIDDEN",
    "TENANT_NOT_FOUND",
    "RESOURCE_NOT_FOUND",
    "VERSION_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "APPROVAL_REQUIRED",
    "APPROVAL_STALE",
    "QUOTA_EXCEEDED",
    "CAPACITY_SATURATED",
    "RETRIEVAL_UNAVAILABLE",
    "INSUFFICIENT_EVIDENCE",
    "SANDBOX_POLICY_DENIED",
    "SANDBOX_LIMIT_EXCEEDED",
    "CONNECTOR_UNKNOWN_OUTCOME",
    "RECOVERABLE_FAILURE",
    "TERMINAL_FAILURE",
}

#: The append-only field set from the contract's "AuditSink.append" signature.
CONTRACT_AUDIT_FIELDS = {
    "event_id",
    "tenant_id",
    "run_id",
    "actor_ref",
    "action",
    "resource_ref",
    "authorization_version",
    "outcome",
    "reason_code",
    "redacted_metadata",
}


# --- RequestPrincipal ------------------------------------------------------ #


def _principal(**overrides: Any) -> RequestPrincipal:
    """Build an immutable principal for the identity/authorization contracts."""
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "user_id": USER,
        "membership_id": MEMBERSHIP,
        "roles": frozenset({"employee"}),
        "scopes": frozenset({Action.READ.value}),
        "authorization_version": 3,
        "session_id": "77777777-7777-4777-8777-777777777777",
        "request_id": "req-t151",
        "run_id": None,
    }
    values.update(overrides)
    return RequestPrincipal(**values)


def test_request_principal_is_frozen_and_ref_excludes_secrets() -> None:
    """A principal is immutable for one attempt; its ref carries ids only."""
    principal = _principal()
    assert [field.name for field in fields(RequestPrincipal)] == [
        "tenant_id",
        "user_id",
        "membership_id",
        "roles",
        "scopes",
        "authorization_version",
        "session_id",
        "request_id",
        "run_id",
    ]
    with pytest.raises(FrozenInstanceError):
        principal.tenant_id = TENANT_B  # type: ignore[misc]

    ref = principal.principal_ref().as_context()
    assert set(ref) == {
        "tenant_id",
        "user_id",
        "membership_id",
        "authorization_version",
    }
    assert principal.session_id not in str(ref)
    assert principal.request_id not in str(ref)


def test_run_create_body_cannot_establish_identity() -> None:
    """Tenant/user identity is never accepted from the run-create body."""
    assert set(RunRequest.model_fields) == {
        "kind",
        "payload",
        "priority_lane",
        "max_attempts",
    }
    body = RunRequest.model_validate(
        {
            "kind": "kb_reindex",
            "payload": {},
            "tenant_id": TENANT_B,
            "user_id": USER,
            "membership_id": MEMBERSHIP,
        }
    )
    dumped = body.model_dump()
    assert not {"tenant_id", "user_id", "membership_id"} & set(dumped)
    assert not {"tenant_id", "user_id", "membership_id"} & set(RunRequest.model_fields)


# --- AuthorizationService -------------------------------------------------- #


def test_allow_only_denies_empty_scopes_and_unknown_actions() -> None:
    """Default deny: no grant allows nothing; unknown actions are not coerced."""
    service = AuthorizationService()
    principal = _principal(scopes=frozenset())
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT)

    decisions = [service.authorize(principal, action, resource) for action in Action]
    assert all(decision.denied for decision in decisions)
    for decision in decisions:
        assert decision.code is not None
        ErrorCode(decision.code)  # declared in the stable vocabulary

    with pytest.raises(UnknownActionError) as excinfo:
        service.authorize(principal, "superuser", resource)
    assert excinfo.value.code == "AUTH_FORBIDDEN"

    with pytest.raises(ApplicationError) as raised:
        decisions[0].raise_for_denial()
    assert raised.value.code == "AUTH_FORBIDDEN"
    assert raised.value.status_code == 403


def test_tenant_visibility_does_not_imply_permission() -> None:
    """Being able to see one's own tenant never substitutes for a grant."""
    service = AuthorizationService()
    resource = ResourceRef(kind="policy", id="policy-1", tenant_id=TENANT)
    assert service.is_tenant_visible(_principal(scopes=frozenset()), TENANT) is True
    assert (
        service.authorize(_principal(scopes=frozenset()), Action.READ, resource).denied
        is True
    )
    assert (
        service.is_tenant_visible(_principal(scopes=frozenset()), TENANT_B) is False
    )


# --- AgentRunState --------------------------------------------------------- #


def _state() -> AgentRunState:
    """Build a fresh ``AgentRunState@1`` for the graph lifecycle contract."""
    return AgentRunState(
        tenant_id=TENANT,
        user_id=USER,
        run_id=RUN_ID,
        thread_id="thread-1",
        tool_budget=ToolBudget(max_tool_calls=3),
        retry_budget=RetryBudget(max_retries_per_node=1),
    )


def test_graph_node_vocabulary_matches_the_contract() -> None:
    """Every documented node (and only those) exists in the shared graph."""
    assert SCHEMA_VERSION == "AgentRunState@1"
    assert {node.value for node in NodeName} == CONTRACT_NODES
    assert set(GraphEntrypoint) >= {
        GraphEntrypoint.CHAT,
        GraphEntrypoint.STREAM,
        GraphEntrypoint.EVAL,
    }
    assert set(ENTRYPOINT_REQUIRED_SCOPE) == set(GraphEntrypoint)


def test_graph_state_is_immutable_and_terminal_states_never_reopen() -> None:
    """State updates are value copies; a terminal state has no outgoing edge."""
    state = _state()
    with pytest.raises(ValidationError):
        state.status = GraphStatus.RUNNING  # type: ignore[misc]

    running = state.transition_to(GraphStatus.RUNNING)
    succeeded = running.transition_to(GraphStatus.SUCCEEDED)
    assert succeeded.status is GraphStatus.SUCCEEDED
    assert succeeded.can_transition_to(GraphStatus.RUNNING) is False
    with pytest.raises(ValueError):
        succeeded.transition_to(GraphStatus.RUNNING)
    with pytest.raises(ValueError):
        state.transition_to(GraphStatus.SUCCEEDED)


# --- DurableJob state machine ---------------------------------------------- #


@pytest_asyncio.fixture
async def job_service(tmp_path) -> AsyncIterator[JobService]:
    """A JobService on a throwaway SQLite database (same pattern as T055)."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    engine = build_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Tenant(id=TENANT, code="t151", name="T151"))
        await session.commit()
    try:
        yield JobService(factory=factory)
    finally:
        await engine.dispose()


async def test_job_transitions_are_legal_and_version_guarded(
    job_service: JobService,
) -> None:
    """Illegal states are rejected and every CAS advances ``version`` exactly once."""
    job = await job_service.enqueue(
        tenant_id=TENANT, kind="kb_index", payload={"kb": "hr"}, idempotency_key="t151"
    )
    assert job.version == 1

    # A queued job has no running owner, so completing it is refused.
    with pytest.raises(JobStateError):
        await job_service.complete(job_id=job.id, worker_id="worker-a")

    leased = await job_service.lease(worker_id="worker-a", lease_seconds=60)
    assert leased is not None and leased.version == job.version + 1
    # Leased is not running: completion is still an illegal transition.
    with pytest.raises(JobStateError):
        await job_service.complete(job_id=job.id, worker_id="worker-a")

    running = await job_service.start(job_id=job.id, worker_id="worker-a")
    assert running.version == leased.version + 1
    done = await job_service.complete(job_id=job.id, worker_id="worker-a")
    assert done.state == "succeeded"
    assert done.version == running.version + 1

    duplicate = await job_service.complete(job_id=job.id, worker_id="worker-a")
    assert duplicate.version == done.version


@pytest.mark.xfail(
    strict=True,
    reason=(
        "JobQueue.lease(worker_id, lanes, lease_duration) is not implemented: "
        "priority_lane is stored and reported, but lease() has no lane filter"
    ),
)
async def test_lease_can_be_restricted_to_a_lane(job_service: JobService) -> None:
    """Contract: a worker leases only the lanes it serves, never another lane's job."""
    await job_service.enqueue(
        tenant_id=TENANT,
        kind="kb_index",
        payload={"lane": "bulk"},
        idempotency_key="t151-bulk",
        priority_lane="bulk",
    )
    await job_service.enqueue(
        tenant_id=TENANT,
        kind="kb_index",
        payload={"lane": "interactive"},
        idempotency_key="t151-interactive",
        priority_lane="interactive",
    )
    leased = await job_service.lease(
        worker_id="worker-a", lease_seconds=60, lanes=("interactive",)
    )
    assert leased is not None
    assert leased.priority_lane == "interactive"


# --- ObjectStore ----------------------------------------------------------- #


def _object_config() -> ObjectStoreConfig:
    """A config that is never used to reach a provider in these tests."""
    return ObjectStoreConfig(
        endpoint_url="http://127.0.0.1:9000",
        region="us-east-1",
        bucket="t151",
        access_key_id="test-key",
        secret_access_key="test-secret",
        tls_enabled=False,
    )


def test_object_key_is_opaque_and_derived_from_the_ids() -> None:
    """Callers address material versions; the key is derived, never supplied."""
    key = derive_object_key(
        tenant_id=TENANT, material_id="material-1", material_version_id="version-1"
    )
    assert key == derive_object_key(
        tenant_id=TENANT, material_id="material-1", material_version_id="version-1"
    )
    assert key != derive_object_key(
        tenant_id=TENANT, material_id="material-1", material_version_id="version-2"
    )
    assert key != derive_object_key(
        tenant_id=TENANT_B, material_id="material-1", material_version_id="version-1"
    )
    shards = key.split("/")
    assert len(shards) == 3 and len(shards[2]) == 64
    for identifier in (TENANT, "material-1", "version-1"):
        assert identifier not in key


async def test_object_store_refuses_a_key_not_derived_for_the_caller() -> None:
    """A leaked key is useless: the tenant must re-derive its own key."""
    store = ObjectStore(_object_config())
    foreign_key = derive_object_key(
        tenant_id=TENANT_B, material_id="material-1", material_version_id="version-1"
    )
    with pytest.raises(CrossTenantObjectAccess):
        await store.read_range(
            tenant_id=TENANT,
            material_id="material-1",
            material_version_id="version-1",
            provider_version_id="provider-v1",
            offset=0,
            length=1,
            object_key=foreign_key,
        )
    with pytest.raises(CrossTenantObjectAccess):
        await store.delete_all_versions(
            tenant_id="", material_id="material-1", material_version_id="version-1"
        )


class _ProviderBytesStore(ObjectStore):
    """ObjectStore whose provider seam returns known bytes (no network)."""

    def __init__(self, config: ObjectStoreConfig, payload: bytes) -> None:
        super().__init__(config)
        self._payload = payload

    async def _head(self, object_key: str) -> dict[str, Any] | None:
        return {
            "VersionId": "provider-v1",
            "ContentLength": len(self._payload),
            "ContentType": "text/plain",
            "ServerSideEncryption": "AES256",
        }

    async def _sha256_of(self, object_key: str, provider_version_id: str) -> str:
        return hashlib.sha256(self._payload).hexdigest()


async def test_verify_upload_compares_the_stored_sha256() -> None:
    """Verification checks the bytes' SHA-256, never the provider ETag."""
    payload = b"policy bytes"
    store = _ProviderBytesStore(_object_config(), payload)
    grant = UploadGrant(
        tenant_id=TENANT,
        material_id="material-1",
        material_version_id="version-1",
        bucket_alias="materials",
        object_key=derive_object_key(
            tenant_id=TENANT, material_id="material-1", material_version_id="version-1"
        ),
        url="https://example.invalid/upload",
        method="PUT",
        expires_at=datetime.now(UTC) + timedelta(seconds=300),
        max_bytes=1024,
        media_type="text/plain",
    )
    expected = hashlib.sha256(payload).hexdigest()
    stored = await store.verify_upload(
        grant=grant,
        expected_sha256=expected,
        expected_size_bytes=len(payload),
        expected_media_type="text/plain",
    )
    assert stored.sha256 == expected

    with pytest.raises(UploadVerificationError):
        await store.verify_upload(
            grant=grant,
            expected_sha256="0" * 64,
            expected_size_bytes=len(payload),
            expected_media_type="text/plain",
        )


# --- SandboxRunner --------------------------------------------------------- #


def _sandbox_request(processor_id: str, *, inputs: tuple[SandboxInput, ...] = ()) -> SandboxRequest:
    """Build the path-free sandbox request; no host path or shell string exists."""
    return SandboxRequest(
        tenant_id=TENANT,
        run_id=RUN_ID,
        workspace_id="workspace-1",
        processor_id=processor_id,
        inputs=inputs,
    )


async def test_sandbox_rejects_an_unallowlisted_processor(tmp_path) -> None:
    """An arbitrary image/command cannot start; the refusal carries a stable code."""
    async def fetch(tenant_id: str, material_id: str, version_id: str) -> bytes:
        return b"never read"

    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({}),
        fetcher=fetch,
    )
    with pytest.raises(ProcessorError) as excinfo:
        await runner.start(_sandbox_request("arbitrary-image"))
    assert excinfo.value.code == "PROCESSOR_UNKNOWN"
    assert not (tmp_path / TENANT / "workspace-1").exists()


async def test_sandbox_stages_only_allowlisted_manifest_inputs(tmp_path) -> None:
    """The allowlisted processor runs over validated, hash-checked staged copies."""
    payload = b"policy bytes"
    digest = hashlib.sha256(payload).hexdigest()

    async def fetch(tenant_id: str, material_id: str, version_id: str) -> bytes:
        assert (tenant_id, material_id, version_id) == (TENANT, "material-1", "version-1")
        return payload

    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({}),
        fetcher=fetch,
    )
    handle = await runner.start(
        _sandbox_request(
            "reimbursement-fill",
            inputs=(
                SandboxInput(
                    material_id="material-1",
                    material_version_id="version-1",
                    relative_path="policy.txt",
                    sha256=digest,
                ),
            ),
        )
    )
    assert (handle.input_dir / "policy.txt").read_bytes() == payload

    with pytest.raises(WorkspaceEscape) as escape:
        await runner.start(
            _sandbox_request(
                "reimbursement-fill",
                inputs=(
                    SandboxInput(
                        material_id="material-1",
                        material_version_id="version-1",
                        relative_path="../escape.txt",
                        sha256=digest,
                    ),
                ),
            )
        )
    assert escape.value.code == "PATH_TRAVERSAL"


def test_processor_registry_is_digest_pinned_and_shell_free() -> None:
    """Processor ids resolve server-side to signed, digest-pinned fixed argv."""
    registry = default_registry()
    for processor_id in registry.ids():
        definition = registry.get(processor_id)
        assert "@sha256:" in definition.image
        assert definition.signature_identity
        assert definition.argv
        assert definition.argv[0].rsplit("/", 1)[-1] not in {"sh", "bash", "zsh"}
        assert "-c" not in definition.argv


# --- AuditSink ------------------------------------------------------------- #


async def test_audit_sink_contract_fields_and_event_id_idempotency() -> None:
    """One row per ``event_id``; the documented field set is persisted as given."""
    store = InMemoryAuditPersistence()
    sink = GuardedAuditSink(store)
    event_id = new_event_id()

    record = await sink.append(
        event_id=event_id,
        tenant_id=TENANT,
        run_id=RUN_ID,
        actor_ref=f"user:{USER}",
        action="approval.execute",
        resource_ref="approval:42",
        authorization_version=3,
        outcome="allowed",
        reason_code=None,
        redacted_metadata={"route": "/api/v2/runs"},
    )
    assert CONTRACT_AUDIT_FIELDS <= {field.name for field in fields(AuditRecord)}
    assert record.event_id == event_id
    assert record.authorization_version == 3
    assert len(store) == 1

    duplicate = await sink.append(
        event_id=event_id,
        tenant_id=TENANT,
        run_id=RUN_ID,
        actor_ref=f"user:{USER}",
        action="approval.execute",
        resource_ref="approval:42",
        authorization_version=4,
        outcome="denied",
        reason_code=None,
        redacted_metadata={"route": "/api/v2/runs/other"},
    )
    assert duplicate == record
    assert duplicate.authorization_version == 3
    assert len(store) == 1

    parameters = inspect.signature(AuditSink.append).parameters
    assert list(parameters) == [
        "self",
        "event_id",
        "tenant_id",
        "run_id",
        "actor_ref",
        "action",
        "resource_ref",
        "authorization_version",
        "outcome",
        "reason_code",
        "redacted_metadata",
    ]


# --- SubmissionConnector --------------------------------------------------- #


async def test_mock_connector_is_marked_mock_and_idempotent_per_key() -> None:
    """One idempotency key maps to one receipt; every response says ``mock``."""
    connector = MockReimbursementConnector()
    first = await connector.submit(
        tenant_id=TENANT,
        idempotency_key="idem-t151",
        destination="expense:demo",
        payload={},
    )
    second = await connector.submit(
        tenant_id=TENANT,
        idempotency_key="idem-t151",
        destination="expense:demo",
        payload={},
    )
    assert first.status == MOCK_STATUS == "mock"
    assert first.is_mock is True
    assert second.receipt_id == first.receipt_id
    assert second.deduplicated is True

    accepted = await connector.reconcile(tenant_id=TENANT, idempotency_key="idem-t151")
    unknown = await connector.reconcile(tenant_id=TENANT, idempotency_key="idem-other")
    assert accepted.already_accepted is True
    assert accepted.receipt_id == first.receipt_id
    assert unknown.already_accepted is False

    assert callable(getattr(connector, "submit"))
    assert callable(getattr(connector, "reconcile"))


# --- SSE ------------------------------------------------------------------- #


async def test_closed_sse_channel_rejects_publish() -> None:
    """A closed connection's channel refuses any further event."""
    channel = BoundedEventChannel(capacity=1, heartbeat_seconds=10.0)
    await channel.publish(
        ChannelEvent("run.progress", {"n": 1}, id="1-0"), timeout=1.0
    )
    await channel.aclose()
    assert channel.closed is True
    with pytest.raises(ChannelClosed):
        await channel.publish(
            ChannelEvent("run.succeeded", {}, id="2-0"), timeout=1.0
        )


def test_durable_milestones_carry_monotonic_sequence_as_the_sse_id() -> None:
    """Durable milestones replay in sequence order with ``d<sequence>`` ids."""

    def row(sequence: int) -> SimpleNamespace:
        return SimpleNamespace(
            sequence=sequence,
            payload={"n": sequence},
            stage="retrieve",
            public_status="running",
            event_type="run.progress",
        )

    events = [DurableRunSnapshot._to_event(row(sequence)) for sequence in (1, 2, 3)]
    assert [event.id for event in events] == ["d1", "d2", "d3"]
    assert [event.data["sequence"] for event in events] == [1, 2, 3]
    assert all(event.id == f"d{event.data['sequence']}" for event in events)

    frame = _frame(events[0].event_type, events[0].data, events[0].id)
    assert frame["id"] == "d1"
    assert frame["event"] == "run.progress"

    assert JOB_STATE_TO_RUN_EVENT["succeeded"] == RUN_SUCCEEDED
    assert JOB_STATE_TO_RUN_EVENT["terminal_failed"] == RUN_FAILED
    assert is_terminal_event(events[0]) is False
    assert is_terminal_event(SimpleNamespace(event_type=RUN_FAILED)) is True


async def test_run_event_sequence_is_monotonic_and_unique(tmp_path) -> None:
    """``(run_id, sequence)`` is append-only: ordered replay, duplicate refused."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'events.db').as_posix()}"
    engine = build_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(Tenant(id=TENANT, code="t151", name="T151"))
            await session.commit()
        async with factory() as session:
            repository = RunEventRepository(session)
            rows = [
                await repository.append(
                    TENANT, "run-t151", "run.started", payload={"n": 1}
                ),
                await repository.append(
                    TENANT, "run-t151", "run.progress", payload={"n": 2}
                ),
                await repository.append(
                    TENANT, "run-t151", "run.succeeded", payload={"n": 3}
                ),
            ]
            assert [row.sequence for row in rows] == [1, 2, 3]
            listed = await repository.list_for_run(TENANT, "run-t151")
            assert [row.sequence for row in listed] == [1, 2, 3]

            with pytest.raises(RunEventSequenceConflictError):
                await repository.append(
                    TENANT, "run-t151", "run.progress", sequence=2
                )
            await session.commit()
    finally:
        await engine.dispose()


# --- Retriever / DocumentIndexer ------------------------------------------- #


def test_retrieval_scope_requires_every_mandatory_pre_ann_filter() -> None:
    """Tenant, KB, active version and embedding version are not optional."""
    assert set(MANDATORY_PRE_ANN_FILTER_FIELDS) == {
        "tenant_id",
        "knowledge_base_id",
        "version_id",
        "embedding_version_id",
        "retrievable",
    }
    expression = RetrievalScope(
        tenant_id=TENANT,
        knowledge_base_ids=("kb-1",),
        embedding_version_id="embedding-1",
        version_ids=("version-1",),
    ).filter_expression()
    for term in MANDATORY_PRE_ANN_FILTER_FIELDS:
        assert term in expression

    incomplete_scopes = [
        RetrievalScope(
            tenant_id="",
            knowledge_base_ids=("kb-1",),
            embedding_version_id="embedding-1",
            version_ids=("version-1",),
        ),
        RetrievalScope(
            tenant_id=TENANT,
            knowledge_base_ids=(),
            embedding_version_id="embedding-1",
            version_ids=("version-1",),
        ),
        RetrievalScope(
            tenant_id=TENANT,
            knowledge_base_ids=("kb-1",),
            embedding_version_id="",
            version_ids=("version-1",),
        ),
        RetrievalScope(
            tenant_id=TENANT,
            knowledge_base_ids=("kb-1",),
            embedding_version_id="embedding-1",
            version_ids=(),
        ),
    ]
    for scope in incomplete_scopes:
        with pytest.raises(ValueError):
            scope.filter_expression()


def test_document_indexer_exposes_the_staged_lifecycle() -> None:
    """Indexing is stage -> verify -> activate; nothing is served before verify."""
    for method in ("stage", "verify", "activate", "deactivate", "delete"):
        assert callable(getattr(VectorIndexer, method))
    stage_parameters = inspect.signature(VectorIndexer.stage).parameters
    assert "embedding_version_id" in stage_parameters
    assert "knowledge_base_id" in stage_parameters
    assert "chunks" in stage_parameters


# --- Error contract -------------------------------------------------------- #


def test_every_documented_error_code_is_declared_once() -> None:
    """The doc's minimum vocabulary exists in the single stable registry."""
    declared = {code.value for code in ErrorCode}
    assert CONTRACT_ERROR_CODES <= declared
    assert all(code.value == code.name for code in ErrorCode)
    for code in CONTRACT_ERROR_CODES:
        semantics = ERROR_SEMANTICS[ErrorCode(code)]
        assert semantics.code is ErrorCode(code)
        assert semantics.status_code >= 400
        assert semantics.message


def test_error_envelope_has_exactly_the_contract_fields() -> None:
    """``success``/``error{code,message,details}``/``request_id`` and nothing else."""
    request = Request(
        {"type": "http", "method": "POST", "path": "/api/v2/runs", "headers": []}
    )
    request.state.request_id = "req-t151"

    envelope = _error_payload(
        request,
        ErrorCode.AUTH_FORBIDDEN,
        "Permission denied",
        {"action": "read"},
    )
    assert set(envelope) == {"success", "error", "request_id"}
    assert envelope["success"] is False
    assert set(envelope["error"]) == {"code", "message", "details"}
    assert envelope["error"]["code"] == "AUTH_FORBIDDEN"
    assert envelope["request_id"] == "req-t151"
