# Internal Contracts: Agent State, Jobs and Sandbox

**Contract version**: 1.0-draft  
**Scope**: Service and worker boundaries not exposed as public HTTP APIs. Concrete Python/TypeScript types must preserve these semantics.

## RequestPrincipal

Every API, repository, graph, worker and side-effect call receives a validated principal. Tenant identity is derived from authenticated membership, never a request body.

```text
RequestPrincipal {
  tenant_id: UUID
  user_id: UUID
  membership_id: UUID
  roles: set<string>
  scopes: set<string>
  authorization_version: int
  session_id: UUID
  request_id: string
  run_id: UUID | null
}
```

Rules:

- Immutable for one request/worker attempt.
- Approval execution loads a fresh principal/authorization version; it does not trust the approval snapshot.
- Logs may include tenant/run identifiers under retention controls, but never credentials or unrestricted personal data.

## AgentRunState

```text
AgentRunState@1 {
  principal_ref
  run_id, run_kind, graph_version, state_version
  thread_id, conversation_id, input
  budget {deadline, max_tool_calls, model_quota_lease}
  memory_snapshot {window, summary_ref, selected_items}
  rewritten_query
  retrieval {filters, strategy, candidates, evidence_refs, gate}
  plan
  tool_results[]
  workspace_ref, change_set_ref
  pending_approval_ref
  output
  errors[]
  retry_counts
  audit_context
}
```

Node contract:

| Node | Required input | Output | Retry boundary |
|---|---|---|---|
| `validate` | principal, run input | validated intent and limits | no retry on validation/RBAC failure |
| `memory_load` | authorized conversation | non-authoritative memory snapshot | bounded transient storage retry |
| `rewrite` | input + history | standalone query | bounded LLM retry within run budget |
| `retrieve` | tenant/KB/version filters | candidate evidence | transient retry; unavailable fails closed |
| `rerank` | candidates | honestly named ranked list | deterministic local fallback only if contract says local lexical fusion |
| `evidence_gate` | ranked evidence | supported/insufficient result | deterministic, no retry |
| `plan_or_tool` | supported evidence + limits | bounded plan/tool requests | each tool has idempotency key and timeout |
| `sandbox` | workspace manifest | staged outputs/change set | job retry by durable state, never host-path input |
| `approval_interrupt` | immutable action digest | pending decision | interrupt may replay node; no side effect before resume |
| `generate` | current evidence + tool results | answer/draft | bounded provider retry |
| `writeback` | successful result | memory/audit updates | idempotent, memory remains non-authoritative |
| `finalize` | all prior state | terminal/recoverable run | compare-and-set once per state version |

Chat, streaming Chat and Eval use these same nodes. Eval may set deterministic mode and disable side effects, but cannot bypass evidence gating.

## JobQueue

```text
JobQueue.enqueue(job_id, tenant_id, kind, payload_digest, idempotency_key)
JobQueue.lease(worker_id, lanes, lease_duration) -> JobLease | none
JobQueue.heartbeat(job_id, lease_token)
JobQueue.is_cancel_requested(job_id) -> bool
JobQueue.complete(job_id, lease_token, result_digest)
JobQueue.fail(job_id, lease_token, error_code, retryable)
```

Rules:

- PostgreSQL `DurableJob` is authority; RabbitMQ messages only wake consumers.
- A lease token and expected state/version are required for mutation.
- Duplicate messages and expired leases cannot duplicate a logical transition.
- Cancellation is cooperative and checked between bounded steps.
- Error payloads are sanitized and classified as retryable or terminal.

## Retriever and DocumentIndexer

```text
Retriever.search(
  principal,
  knowledge_base_ids,
  active_document_versions,
  embedding_version,
  query,
  top_k,
) -> RetrievalResult

DocumentIndexer.stage(version_id, embedding_version, idempotency_key)
DocumentIndexer.verify(manifest_id) -> VerificationResult
DocumentIndexer.activate(manifest_id, expected_active_version)
DocumentIndexer.deactivate_and_delete(manifest_id)
```

Rules:

- Tenant, KB, active document version and embedding version filters are mandatory and applied inside Milvus before retrieval.
- Retrieval unavailability returns a typed failure; callers must not generate a policy conclusion without evidence.
- Activation/deletion follows the state machine in [data-model.md](../data-model.md).

## ObjectStore

```text
ObjectStore.create_upload(principal, material_version_id, declared_metadata)
ObjectStore.verify_upload(material_version_id, expected_sha256)
ObjectStore.open_read(principal, material_version_id, byte_range?)
ObjectStore.delete_all_versions(material_version_id, idempotency_key)
```

Rules:

- Callers identify material versions, never arbitrary bucket/key pairs.
- Upload capabilities are short-lived, content-length/media constrained and bound to one staged version.
- Verification checks provider VersionId, size, SHA-256, type and scan result before availability.
- Physical deletion includes every version and delete marker and remains recoverable until reconciliation passes.

## SandboxRunner

```text
SandboxRunner.start(
  principal,
  workspace_id,
  input_manifest_digest,
  processor_id,
  resource_policy_id,
  idempotency_key,
) -> DurableJobRef

SandboxRunner.cancel(workspace_id, reason)
SandboxRunner.collect(workspace_id) -> OutputManifest
```

The runner accepts no host path, shell string, arbitrary image, URL, credential or network destination.

Mandatory execution policy:

- Ephemeral isolated job; non-root, read-only root, dropped capabilities, seccomp and runtime isolation.
- No service-account token, host mount, cloud credentials or default network egress.
- Inputs are immutable copies selected in `WorkspaceInput`; outputs only in controlled output volume.
- Processor ID resolves server-side to a signed allowlisted image and fixed argv template.
- CPU, memory, PID, time and storage limits are mandatory; limit breach is a typed terminal/recoverable result.
- Output collection rejects symlinks, abnormal names and manifest/hash mismatches.

## AuthorizationService

```text
AuthorizationService.authorize(principal, action, resource_ref) -> Allow | Deny
AuthorizationService.authorization_version(principal) -> int
```

Required actions include `read`, `edit`, `approve`, `submit`, `publish`, `delete`, `admin`, `cross_tenant_admin`. Every side effect is authorized immediately before execution. Client visibility never implies permission.

## SubmissionConnector

```text
SubmissionConnector.submit(
  principal,
  approval_id,
  action_digest,
  destination_ref,
  exact_material_versions,
  expected_destination_version,
  idempotency_key,
) -> Submitted | RecoverableFailure | UnknownOutcome | TerminalFailure

SubmissionConnector.reconcile(idempotency_key) -> ReconciliationResult
```

Rules:

- Connector validates an approved, unexpired, unconsumed matching digest and fresh RBAC.
- Mock connectors return `status=mock` and cannot be described as production integrations.
- `UnknownOutcome` must reconcile before retry; provider receipt is stored without secrets.
- One idempotency key maps to one digest and destination for its lifetime.

## AuditSink

```text
AuditSink.append(event_id, tenant_id, run_id, actor_ref, action, resource_ref,
                 authorization_version, outcome, reason_code, redacted_metadata)
```

Append is idempotent by `event_id`. Audit failure on approval or external side effects blocks completion and leaves the operation recoverable; it is never silently ignored.

## SSE Event Semantics

Public event shape is specified by [openapi.yaml](openapi.yaml). Additional invariants:

- `(run_id, sequence)` is monotonic and unique; `event_id` is the SSE `id`.
- Heartbeats carry no business transition.
- Durable milestones can be reconstructed after Redis replay expiry; token deltas need not be durable.
- Slow consumers receive bounded buffering. Low-value progress may be coalesced, but terminal, approval and error events cannot be dropped.
- Disconnect cleanup occurs independently of whether the durable run is cancelled or continues.

## Error Contract

All boundaries use stable codes, at minimum:

```text
AUTH_FORBIDDEN, TENANT_NOT_FOUND, RESOURCE_NOT_FOUND,
VERSION_CONFLICT, IDEMPOTENCY_CONFLICT, APPROVAL_REQUIRED,
APPROVAL_STALE, QUOTA_EXCEEDED, CAPACITY_SATURATED,
RETRIEVAL_UNAVAILABLE, INSUFFICIENT_EVIDENCE,
SANDBOX_POLICY_DENIED, SANDBOX_LIMIT_EXCEEDED,
CONNECTOR_UNKNOWN_OUTCOME, RECOVERABLE_FAILURE, TERMINAL_FAILURE
```

A typed error states whether retry is safe and may include a server-controlled retry delay. It never reveals another tenant's existence, storage keys, host paths, credentials or raw provider payloads.

