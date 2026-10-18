"""Contract tests for the v2 OpenAPI draft (phase-10 T150).

What this suite proves
----------------------
``specs/001-enterprise-agent-refactor/contracts/openapi.yaml`` describes the
*same* tenant-scoped surface the assembled application actually mounts, not an
aspirational subset:

* the path+method set equals ``create_app(Settings(...)).openapi()`` under
  ``/api/v2`` (23 paths, 24 operations);
* every operation inherits the bearer requirement;
* ``Idempotency-Key`` is declared only where the runtime reads it (the route
  itself rejects a missing key on ``POST /runs`` with 400, so the parameter is
  required there; no other v2 route declares that header);
* the runtime error envelope matches the contract ``Problem`` schema;
* the admin payload schemas carry the top-level keys the service returns.

Runtime/contract disagreements found while writing this suite
-------------------------------------------------------------
The contract is a draft, so in every case below the YAML was corrected to the
running behaviour rather than the backend being changed:

1. **Error envelope is not RFC 9457.** ``backend/app/core/exceptions.py`` emits
   ``{"success": false, "error": {"code", "message", "details"},
   "request_id": ...}`` as ``application/json``. The old contract ``Problem``
   required ``type``/``title``/``status``; those keys are never present. The
   schema now documents the real envelope (still named ``Problem``).
2. **``POST /runs`` is not a 202 + ``{kind, input}`` request.** The runtime body
   is ``RunRequest`` = ``kind``/``payload``/``priority_lane``/``max_attempts``,
   and the response is 201 with the durable-job view (``state``/``attempts``/
   ``job_id``), not the old ``status``/``stage``/``evidence_gate`` run view.
3. **``Idempotency-Key`` is required by the route, not by ``Header(...)``.**
   ``create_run`` declares ``Header(default=None)`` and then raises 400
   ``IDEMPOTENCY_KEY_INVALID``. The contract marks the parameter required
   because omitting it always fails, while other operations (cancel, approval,
   materials, admin POSTs) never read the header and no longer declare it.
4. **``POST /materials`` has no ``Idempotency-Key``.** It requires
   ``knowledge_base_id`` and returns ``material_version_id`` (not the old
   ``version_id``).
5. **Approval decisions have no idempotency header either.** The reviewed
   ``action_digest`` and ``expected_version`` are fields of
   ``ApprovalDecision`` (the request), not of the ``Approval`` response. The
   runtime decision response is ``approval_id``/``run_id``/``status``/
   ``decided_by``/``decided_at``/``version``.
6. **Admin payloads are page envelopes.** Every list returns
   ``{items, total, limit, offset}``; ``/admin/roles`` returns ``{items,total}``.
   The old contract had no admin schemas at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support_admin import (
    ADMIN,
    EMPLOYEE,
    TENANT_ALPHA,
    admin_client,
    bearer,
)

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "001-enterprise-agent-refactor"
    / "contracts"
    / "openapi.yaml"
)

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})
API_PREFIX = "/api/v2"

ADMIN_SCHEMA_KEYS: dict[str, set[str]] = {
    "AdminRoleList": {"items", "total"},
    "GrantList": {"items", "total", "limit", "offset"},
    "QuotaSummary": {"policies", "usage", "live_leases"},
    "HealthSummary": {
        "jobs",
        "oldest_queued_at",
        "documents",
        "submissions",
        "workspaces",
        "retrieval",
        "reconciliation",
    },
    "RunList": {"items", "total", "limit", "offset"},
    "RunTimeline": {"run", "items", "total"},
    "RecoveryResult": {
        "run_id",
        "job_id",
        "previous_state",
        "state",
        "attempts",
        "max_attempts",
        "version",
    },
    "ReconciliationList": {"items", "total", "limit", "offset"},
    "CapacityList": {"items", "total", "limit", "offset"},
}


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    """Load the checked-in YAML draft exactly as a client would consume it."""
    with CONTRACT_PATH.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    """A fully assembled app over a temp SQLite database (seeded two-tenant fixture)."""
    tmp_path = tmp_path_factory.mktemp("contract-v2")
    with admin_client(tmp_path) as test_client:
        yield test_client


def _resolve(contract: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """Resolve one local ``$ref`` (the document only uses ``#/...`` references)."""
    ref = value.get("$ref")
    if ref is None:
        return value
    assert ref.startswith("#/"), ref
    node: Any = contract
    for part in ref[2:].split("/"):
        node = node[part]
    assert isinstance(node, dict), ref
    return node


def _contract_operations(contract: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(path, METHOD, operation)`` for every operation in the contract."""
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            if method.lower() in HTTP_METHODS:
                yield path, method.upper(), operation


def _operation_parameters(
    contract: dict[str, Any], operation: dict[str, Any]
) -> list[dict[str, Any]]:
    return [_resolve(contract, parameter) for parameter in operation.get("parameters", [])]


def _runtime_header_operations(client: Any) -> set[tuple[str, str]]:
    """Return the v2 operations whose route signature declares Idempotency-Key.

    FastAPI 0.139 stores included routers as opaque ``_IncludedRouter`` wrappers,
    so walking ``app.routes`` no longer reaches the ``APIRoute`` objects. The
    generated OpenAPI document is built from those route signatures and is the
    stable runtime truth here.
    """
    found: set[tuple[str, str]] = set()
    for path, item in client.app.openapi()["paths"].items():
        if not path.startswith(API_PREFIX):
            continue
        for method, operation in item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("name") == "Idempotency-Key" and parameter.get("in") == "header":
                    found.add((path, method.upper()))
    return found


def _assert_problem_payload(payload: dict[str, Any], problem: dict[str, Any]) -> None:
    """Assert the runtime body satisfies the contract ``Problem`` schema."""
    assert set(problem["required"]) <= set(payload)
    assert payload["success"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert {"code", "message"} <= set(error)
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert payload["request_id"] is None or isinstance(payload["request_id"], str)
    # The handler never emits RFC 9457 members; a regression that adds them (or
    # a schema edit that starts requiring them) must fail this suite.
    assert "type" not in payload
    assert "title" not in payload
    assert "status" not in payload


def test_contract_path_and_method_set_equals_runtime(contract: dict[str, Any], client: Any) -> None:
    runtime_spec = client.app.openapi()
    runtime_operations = {
        (path, method.upper())
        for path, item in runtime_spec["paths"].items()
        if path.startswith(API_PREFIX)
        for method in item
        if method.lower() in HTTP_METHODS
    }
    contract_operations = {
        (f"{API_PREFIX}{path}" if not path.startswith(API_PREFIX) else path, method)
        for path, method, _ in _contract_operations(contract)
    }

    assert contract_operations == runtime_operations
    assert len({path for path, _ in runtime_operations}) == 23
    assert contract["info"]["version"] == "2.1.0-draft"
    assert contract["servers"] == [{"url": API_PREFIX}]


def test_every_v2_operation_requires_bearer(contract: dict[str, Any]) -> None:
    global_security = contract.get("security", [])
    assert {"bearerAuth": []} in global_security

    missing = [
        f"{method} {path}"
        for path, method, operation in _contract_operations(contract)
        if {"bearerAuth": []} not in operation.get("security", global_security)
    ]
    assert missing == []


def test_idempotency_header_only_where_runtime_reads_it(
    contract: dict[str, Any], client: Any
) -> None:
    runtime_operations = _runtime_header_operations(client)
    assert runtime_operations == {(f"{API_PREFIX}/runs", "POST")}

    # The header is optional at the FastAPI signature level (Header(default=None))
    # but the route rejects a missing key with 400, so the semantic requirement is
    # real and the contract is right to mark the parameter required.
    missing_key = client.post(
        f"{API_PREFIX}/runs",
        json={"kind": "chat", "payload": {}},
        headers=bearer(client, TENANT_ALPHA, ADMIN),
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"

    declared: set[tuple[str, str]] = set()
    for path, method, operation in _contract_operations(contract):
        for parameter in _operation_parameters(contract, operation):
            if parameter.get("name") == "Idempotency-Key" and parameter.get("in") == "header":
                declared.add((f"{API_PREFIX}{path}", method))
                # Omitting it is a 400 on /runs, so it is genuinely required.
                assert parameter.get("required") is True

    assert declared == runtime_operations

    # Materials and the admin POSTs take no idempotency header; assert the
    # absence explicitly so the old over-broad contract cannot creep back.
    materials_post = contract["paths"]["/materials"]["post"]
    assert "Idempotency-Key" not in {
        parameter.get("name") for parameter in _operation_parameters(contract, materials_post)
    }


def test_admin_operations_are_documented(contract: dict[str, Any]) -> None:
    operations = {
        (path, method): operation
        for path, method, operation in _contract_operations(contract)
        if path.startswith("/admin/")
    }
    assert len(operations) == 13

    for (path, method), operation in operations.items():
        assert operation.get("tags") == ["Admin"], f"{method} {path}"
        assert isinstance(operation.get("operationId"), str)
        assert operation["operationId"]
        assert isinstance(operation.get("summary"), str)
        assert operation["summary"]

    filters = {
        ("/admin/grants", "GET"): {"limit", "offset", "member_id"},
        ("/admin/runs", "GET"): {"limit", "offset", "status", "kind"},
        ("/admin/reconciliation", "GET"): {"limit", "offset", "state", "issue_kind"},
        ("/admin/runs/{run_id}/audit", "GET"): {"limit"},
        ("/admin/capacity", "GET"): {"limit", "offset"},
    }
    for key, expected in filters.items():
        operation = operations[key]
        names = {
            parameter.get("name")
            for parameter in _operation_parameters(contract, operation)
        }
        assert expected <= names, f"{key[1]} {key[0]}"

    post_with_body = {
        ("/admin/grants", "POST"),
        ("/admin/grants/{grant_id}/revoke", "POST"),
        ("/admin/runs/{run_id}/recover", "POST"),
        ("/admin/reconciliation/{issue_id}/retry", "POST"),
        ("/admin/reconciliation/{issue_id}/resolve", "POST"),
    }
    for key in post_with_body:
        assert "requestBody" in operations[key], f"{key[1]} {key[0]}"


def test_all_4xx_and_5xx_responses_use_problem(contract: dict[str, Any]) -> None:
    problem_ref = "#/components/schemas/Problem"
    for path, method, operation in _contract_operations(contract):
        for status, response in operation.get("responses", {}).items():
            if status[:1] not in {"4", "5"}:
                continue
            resolved = _resolve(contract, response)
            schema = resolved["content"]["application/json"]["schema"]
            assert schema.get("$ref") == problem_ref, f"{method} {path} {status}"


def test_runtime_error_bodies_match_problem(contract: dict[str, Any], client: Any) -> None:
    problem = contract["components"]["schemas"]["Problem"]
    admin_headers = bearer(client, TENANT_ALPHA, ADMIN)
    employee_headers = bearer(client, TENANT_ALPHA, EMPLOYEE)

    unauthorized = client.get(f"{API_PREFIX}/principal")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["content-type"].startswith("application/json")
    _assert_problem_payload(unauthorized.json(), problem)

    # Invalid body *and* no Idempotency-Key: FastAPI validates the body first, so
    # this is the 422 path the contract documents (not a 400).
    validation = client.post(
        f"{API_PREFIX}/runs", json={"unexpected": True}, headers=admin_headers
    )
    assert validation.status_code == 422
    _assert_problem_payload(validation.json(), problem)
    assert isinstance(validation.json()["error"]["details"], list)
    assert validation.json()["error"]["code"] == "VALIDATION_ERROR"

    forbidden = client.get(f"{API_PREFIX}/admin/roles", headers=employee_headers)
    assert forbidden.status_code == 403
    _assert_problem_payload(forbidden.json(), problem)
    assert forbidden.json()["error"]["code"] == "AUTH_FORBIDDEN"

    missing = client.get(f"{API_PREFIX}/runs/does-not-exist", headers=admin_headers)
    assert missing.status_code == 404
    _assert_problem_payload(missing.json(), problem)

    # A real 409: same (tenant, kind, key) with a different payload.
    idempotency_key = "contract-openapi-key-0001"
    first = client.post(
        f"{API_PREFIX}/runs",
        json={"kind": "chat", "payload": {"message": "one"}},
        headers={**admin_headers, "Idempotency-Key": idempotency_key},
    )
    assert first.status_code == 201
    assert {"run_id", "job_id", "tenant_id", "kind", "state", "attempts"} <= set(
        first.json()
    )
    conflict = client.post(
        f"{API_PREFIX}/runs",
        json={"kind": "chat", "payload": {"message": "different"}},
        headers={**admin_headers, "Idempotency-Key": idempotency_key},
    )
    assert conflict.status_code == 409
    _assert_problem_payload(conflict.json(), problem)
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_admin_payload_schemas_match_service_views(contract: dict[str, Any]) -> None:
    schemas = contract["components"]["schemas"]
    assert set(ADMIN_SCHEMA_KEYS) <= set(schemas)
    for name, expected_keys in ADMIN_SCHEMA_KEYS.items():
        properties = set(schemas[name].get("properties", {}))
        assert expected_keys <= properties, name
        assert expected_keys <= set(schemas[name].get("required", [])), name


def test_approval_and_material_constraints(contract: dict[str, Any]) -> None:
    schemas = contract["components"]["schemas"]

    # action_digest/expected_version are decision *inputs*, so they are required
    # on ApprovalDecision; the response Approval carries the decided identity.
    assert {"action_digest", "expected_version"} <= set(schemas["ApprovalDecision"]["required"])
    assert {"approval_id", "run_id", "status", "version"} <= set(schemas["Approval"]["required"])

    # MaterialUpload returns material_version_id, not the old version_id.
    assert {"material_version_id", "version_number", "purpose", "upload"} <= set(
        schemas["MaterialUpload"]["required"]
    )
    assert "version_id" not in schemas["MaterialUpload"]["required"]
