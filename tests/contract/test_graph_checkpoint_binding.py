"""Failing contracts for authorized graph checkpoint binding (T038)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.app.auth.principal import RequestPrincipal
from backend.app.graph.checkpoints import (
    CHECKPOINT_BINDING_SCHEMA_VERSION,
    GraphCheckpointBinding,
    GraphCheckpointBindingError,
    InMemoryGraphCheckpointBindingStore,
)
from backend.app.graph.service import GraphService


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
USER_A = "user-a"
USER_B = "user-b"
RUN_A = "run-a"
THREAD_A = "opaque-thread-a"


def make_principal(
    *,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    run_id: str = RUN_A,
    authorization_version: int = 3,
) -> RequestPrincipal:
    return RequestPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=f"membership-{user_id}",
        roles=frozenset({"employee"}),
        scopes=frozenset({"graph:invoke", "graph:stream", "graph:resume"}),
        authorization_version=authorization_version,
        session_id="session-a",
        request_id="request-a",
        run_id=run_id,
    )


def make_binding() -> GraphCheckpointBinding:
    return GraphCheckpointBinding(
        tenant_id=TENANT_A,
        user_id=USER_A,
        run_id=RUN_A,
        thread_id=THREAD_A,
        graph_schema_version="AgentRunState@1",
        checkpoint_schema_version=CHECKPOINT_BINDING_SCHEMA_VERSION,
        authorization_version=3,
        latest_checkpoint_ref="checkpoint-1",
    )


def test_thread_identifier_is_opaque_and_not_derived_from_identity() -> None:
    binding = GraphCheckpointBinding.create(
        tenant_id=TENANT_A,
        user_id=USER_A,
        run_id=RUN_A,
        graph_schema_version="AgentRunState@1",
        authorization_version=3,
    )

    assert binding.thread_id
    assert TENANT_A not in binding.thread_id
    assert USER_A not in binding.thread_id
    assert RUN_A not in binding.thread_id


def test_binding_carries_tenant_user_run_and_schema_versions() -> None:
    binding = make_binding()

    assert binding.tenant_id == TENANT_A
    assert binding.user_id == USER_A
    assert binding.run_id == RUN_A
    assert binding.graph_schema_version == "AgentRunState@1"
    assert binding.checkpoint_schema_version == CHECKPOINT_BINDING_SCHEMA_VERSION


@pytest.mark.parametrize("operation", ["invoke", "stream", "resume"])
@pytest.mark.parametrize(
    "principal",
    [
        make_principal(tenant_id=TENANT_B),
        make_principal(user_id=USER_B),
        make_principal(run_id="run-b"),
    ],
)
@pytest.mark.asyncio
async def test_graph_operations_reject_mismatched_binding_identity(
    operation: str, principal: RequestPrincipal
) -> None:
    store = InMemoryGraphCheckpointBindingStore([make_binding()])
    service = GraphService(checkpoint_bindings=store)

    graph_operation = getattr(service, operation)
    with pytest.raises(GraphCheckpointBindingError, match="authorized|binding"):
        await graph_operation(
            principal=principal,
            run_id=RUN_A,
            thread_id=THREAD_A,
            input_payload={"message": "hello"},
        )


@pytest.mark.parametrize("operation", ["invoke", "stream", "resume"])
@pytest.mark.asyncio
async def test_graph_operations_reject_unknown_thread_without_disclosure(
    operation: str,
) -> None:
    service = GraphService(
        checkpoint_bindings=InMemoryGraphCheckpointBindingStore([make_binding()])
    )

    graph_operation = getattr(service, operation)
    with pytest.raises(GraphCheckpointBindingError) as caught:
        await graph_operation(
            principal=make_principal(),
            run_id=RUN_A,
            thread_id="opaque-thread-unknown",
            input_payload={"message": "hello"},
        )

    assert caught.value.public_code == "GRAPH_CHECKPOINT_NOT_FOUND"
    assert TENANT_A not in str(caught.value)
    assert USER_A not in str(caught.value)


@pytest.mark.parametrize("operation", ["invoke", "stream", "resume"])
@pytest.mark.asyncio
async def test_graph_operations_reject_stale_authorization_version(
    operation: str,
) -> None:
    stale = replace(make_principal(), authorization_version=2)
    service = GraphService(
        checkpoint_bindings=InMemoryGraphCheckpointBindingStore([make_binding()])
    )

    graph_operation = getattr(service, operation)
    with pytest.raises(GraphCheckpointBindingError) as caught:
        await graph_operation(
            principal=stale,
            run_id=RUN_A,
            thread_id=THREAD_A,
            input_payload={"message": "hello"},
        )

    assert caught.value.public_code == "AUTHORIZATION_STALE"


def test_unknown_checkpoint_schema_version_is_rejected() -> None:
    payload = make_binding().to_dict()
    payload["checkpoint_schema_version"] = "GraphCheckpointBinding@999"

    with pytest.raises(GraphCheckpointBindingError, match="schema version"):
        GraphCheckpointBinding.from_dict(payload)


def test_unknown_graph_state_schema_version_is_rejected() -> None:
    payload = make_binding().to_dict()
    payload["graph_schema_version"] = "AgentRunState@999"

    with pytest.raises(GraphCheckpointBindingError, match="schema version"):
        GraphCheckpointBinding.from_dict(payload)
