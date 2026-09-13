# Data Model: 企业级智能体重构

**Date**: 2026-09-12  
**Spec**: [spec.md](spec.md)

## Modeling Rules

- 生产权威业务记录位于 PostgreSQL；每个租户对象必须含 `tenant_id`，外键和唯一约束在可能时包含租户维度。
- `run_id` 是一次业务执行的关联标识，不代替数据库主键；API、图、任务、审批、提交、检索和审计均传播它。
- 时间使用 UTC；状态由受约束枚举表达；并发更新使用 `version` 或数据库 compare-and-set，禁止 last-write-wins。
- 原始/生成文件字节位于版本化对象存储；Milvus 只保存分块和检索元数据；数据库保存双方引用、校验和及状态。
- 可恢复步骤保存 `attempt_count`、`next_attempt_at`、`last_error_code`；用户可见错误不保存敏感路径、凭据或其他租户信息。
- 物理删除采用显式状态机和核对，不以数据库软删除冒充完成。

## Identity and Authorization

### Tenant

| Field | Rules |
|---|---|
| `id` | UUID primary key |
| `code`, `name` | `code` globally unique; name required |
| `status` | `active`, `suspended`, `deleting` |
| `resource_policy_id` | Active quota/retention policy |
| `created_at`, `updated_at` | UTC audit timestamps |

Relationships: owns users, grants, conversations, knowledge, runs, files, jobs, quotas and audits. Suspended tenants cannot start work or approve side effects.

### User

| Field | Rules |
|---|---|
| `id`, `tenant_id` | Composite tenant ownership; external identity cannot move tenants |
| `external_subject` | Unique within tenant and identity issuer |
| `display_name`, `status` | Status is `active`, `suspended`, `disabled` |
| `created_at`, `updated_at` | UTC timestamps |

### Role and UserRoleGrant

`Role` contains tenant-owned `id`, stable `code`, name and a versioned set of permission actions/resources. `UserRoleGrant` links tenant, user and role with scope, `valid_from`, optional `expires_at`, grantor and revocation metadata.

Validation:

- Permissions are allow-only and least-privilege; no wildcard external submission for ordinary employees.
- Every protected read/write is re-authorized server-side; approval execution rechecks current grants.
- Cross-tenant administration requires a separate explicit grant and audit event.

## Runs, Graph State and Evidence

### AgentRun

| Field | Rules |
|---|---|
| `id`, `run_id` | UUID PK plus globally unique public correlation ID |
| `tenant_id`, `user_id`, `conversation_id` | Required ownership; conversation optional for Eval/system jobs |
| `kind` | `chat`, `eval`, `file_workflow`, `reconciliation` |
| `thread_id` | Opaque LangGraph thread identifier; unique within tenant |
| `status` | See state machine below |
| `input_snapshot`, `result_snapshot` | Versioned JSON schemas; exclude file bytes/secrets |
| `evidence_gate` | `supported`, `insufficient_evidence`, `not_applicable` |
| `deadline_at`, `tool_call_count`, `graph_version` | Enforce total runtime and bounded tool loop |
| `created_at`, `started_at`, `finished_at` | UTC lifecycle timestamps |
| `version` | Optimistic concurrency token |

AgentRun state transitions:

```text
queued → running → waiting_approval → running
queued/running/waiting_approval → cancel_requested → cancelled
running → succeeded | recoverable_failed | terminal_failed | timed_out
recoverable_failed → queued
```

Terminal states cannot transition except through an audited administrative recovery that creates a new run. Disconnecting SSE does not itself decide cancellation.

### RunEvent

Append-only event with tenant/run, monotonic `sequence`, globally unique `event_id`, `event_type`, stage, public status, sanitized payload, `occurred_at` and trace context. Unique `(run_id, sequence)` supports ordered replay. Durable milestones are PostgreSQL records; short-lived token/progress fanout may live in Redis Streams.

### GraphCheckpointBinding

Maps tenant, user, run and opaque `thread_id` to graph/checkpoint schema versions, latest checkpoint reference and authorization version. Checkpoint payload remains in the LangGraph PostgreSQL saver schema; callers never address a thread without first resolving this binding.

### EvidenceReference

| Field | Rules |
|---|---|
| `id`, `tenant_id`, `run_id` | Required ownership and provenance |
| `knowledge_base_id`, `document_id`, `material_version_id` | Must reference one immutable active version |
| `chunk_id`, `locator`, `content_hash` | Stable citation location and integrity |
| `retrieval_strategy`, `rank`, `score` | Honest strategy name; nullable score where unavailable |
| `embedding_version_id` | Version used by the run |

Evidence is immutable for a completed run. Memory references are recorded separately and can never satisfy the policy evidence gate.

## Knowledge, Materials and Retrieval

### Material

Logical enterprise item with `id`, tenant, owner/source type (`policy`, `user_upload`, `generated_draft`), current lifecycle status and active version pointer. A formal policy item marks originals as read-only.

### MaterialVersion

| Field | Rules |
|---|---|
| `id`, `tenant_id`, `material_id` | Immutable version identity |
| `version_number` | Unique per material; monotonically increasing |
| `source_version_id` | Parent version for edits; null only for roots |
| `object_version_id` | Required after upload validation |
| `sha256`, `size_bytes`, `media_type` | Must match object metadata |
| `status` | `staging`, `scanning`, `indexing`, `available`, `quarantined`, `superseded`, `deleting` |
| `created_by`, `created_at` | Provenance |

Published fields are immutable. Editing creates a new row; generated versions remain drafts until approved and are not retrievable as formal evidence.

### ObjectVersion

Stores tenant, bucket alias, opaque object key, provider `version_id`, hash, byte size, media type, encryption metadata, scan status and retention/deletion state. API clients receive material/version IDs or signed task-scoped access, never arbitrary object keys.

### EmbeddingVersion

Defines provider/model identifier, dimensions, normalization, chunking policy version, created time and lifecycle (`building`, `active`, `retired`). Exactly one active retrieval version exists per knowledge base and migration cohort.

### VectorManifest

Maps tenant, knowledge base, document/material version and embedding version to Milvus collection/database, deterministic vector ID range or chunk IDs, expected count, indexed count, hash, `retrievable`, activation time and deletion status.

Validation:

- Every Milvus row contains tenant, KB, document, document version, chunk and embedding version.
- A query must filter tenant + allowed KB + active immutable version + `retrievable=true` before ANN search.
- Manifest activation is compare-and-set; old and new versions are never simultaneously authoritative.

### ReconciliationIssue

Captures store pair, resource/version ID, issue kind (`missing_object`, `orphan_object`, `missing_vector`, `orphan_vector`, `missing_chunk`, `version_drift`), observed/expected fingerprints, severity, state, attempts, resolution and audit timestamps.

## File Workspace, Change and Approval

### TaskWorkspace

| Field | Rules |
|---|---|
| `id`, `tenant_id`, `run_id`, `user_id`, `session_id` | Bound at creation; immutable ownership |
| `status` | `provisioning`, `ready`, `processing`, `changes_ready`, `awaiting_approval`, `closed`, `expired`, `failed` |
| `sandbox_job_ref` | Opaque infrastructure reference, never a host path |
| `input_manifest_digest`, `policy_snapshot` | Exact selected versions and resource policy |
| `expires_at`, `created_at`, `closed_at` | Lifecycle and cleanup |

### WorkspaceInput

Join from workspace to an explicitly selected `MaterialVersion`; includes read/edit purpose and staged hash. It cannot be expanded by model output. Formal policy inputs are always read-only.

### ChangeSet and ChangeSetItem

`ChangeSet` belongs to tenant/workspace/run and records source manifest digest, evidence set digest, summary, expected side-effect class and state (`draft`, `ready`, `awaiting_approval`, `approved`, `rejected`, `invalidated`, `applied`). Items identify source and proposed material versions, operation, normalized relative path, before/after hashes and diff artifact.

Validation:

- Paths are normalized before persistence and must be unique within the workspace.
- Any input, output, destination, evidence or permission-snapshot change invalidates prior approval.
- Concurrent edits require the expected source version; mismatch produces conflict, never silent overwrite.

### ApprovalRequest

| Field | Rules |
|---|---|
| `id`, `tenant_id`, `run_id`, `change_set_id` | One immutable review target |
| `action`, `destination` | Typed allowlisted action and destination reference |
| `action_digest` | Hash of action, exact files/hashes, source versions, destination and side effects |
| `requested_by`, `decided_by` | Both tenant members with required authority |
| `authorization_version` | Snapshot for explanation; current authorization still rechecked |
| `status` | `pending`, `approved`, `rejected`, `expired`, `invalidated`, `consumed` |
| `expires_at`, `decided_at`, `reason` | Required lifecycle fields |

Only `pending → approved|rejected|expired|invalidated` is allowed. `approved → consumed|invalidated|expired`; consumption occurs atomically with creation/claim of the corresponding submission. Approval cannot be reused for a different digest.

### SubmissionJob

Unique execution record with tenant/run/approval, connector and destination, `idempotency_key`, expected target version, state, attempt count, provider receipt, sanitized result, next attempt and timestamps.

```text
ready → executing → succeeded
executing → recoverable_failed → ready
ready/executing → cancelled | terminal_failed | unknown_outcome
unknown_outcome → reconciling → succeeded | ready | terminal_failed
```

Unique `(tenant_id, connector_id, idempotency_key)` guarantees at-most-one accepted business action. Unknown provider outcomes must be reconciled before retrying.

## Durable Work and Quotas

### DurableJob

Stores tenant/run, job kind, payload schema version and digest, state (`queued`, `leased`, `running`, `cancel_requested`, `succeeded`, `recoverable_failed`, `terminal_failed`, `cancelled`), priority lane, lease owner/expiry, heartbeat, attempts, deadlines and result/error references. Unique idempotency key prevents duplicate logical work.

### OutboxEvent

Created in the same database transaction as the business change; contains aggregate identity/version, event type, payload, delivery state, attempts and next delivery time. Unique aggregate-version-event prevents duplicate transition publication.

### IdempotencyRecord

Scopes client or connector key to tenant, operation, request digest, canonical response/status and expiry. Reusing a key with a different digest is a conflict; in-progress duplicates observe or join the original operation.

### QuotaPolicy, QuotaLease and UsageRecord

- `QuotaPolicy`: versioned limits by tenant/user/workload for requests, tokens, concurrency, queue admission and time window.
- `QuotaLease`: durable correlation for a short Redis concurrency lease; includes owner, resource, acquired/expiry/released timestamps and outcome.
- `UsageRecord`: append-only actual/reserved model tokens, requests, latency and provider/model dimensions tied to run and tenant.

Redis coordinates atomic admission, but PostgreSQL remains authority for policies, final usage and audit. Lease expiry is not permission to duplicate a non-idempotent side effect.

## Audit and Capacity Evidence

### AuditEvent

Append-only event containing event ID, tenant, run/request/trace IDs, actor and authorization version, resource/action, outcome, reason/error code, redacted metadata and timestamp. Sensitive content, raw credentials, host paths and unrestricted file bodies are prohibited. Approval and external side-effect events are retained according to enterprise policy and cannot be edited in place.

### CapacityTestRun

| Field | Rules |
|---|---|
| `id`, `commit_sha`, `scenario`, `suite_version` | Reproducible identity |
| `environment_manifest`, `topology`, `hardware`, `dataset_manifest` | Required evidence |
| `llm_mode` | `deterministic_mock` or `real_provider`; results cannot be merged |
| `target_profile`, `started_at`, `duration_seconds` | Workload definition |
| `raw_artifact_uri`, `raw_artifact_sha256` | Immutable raw result pointer |
| `summary_metrics` | Throughput, p50/p95/p99, errors, disconnects, queue and resource data |
| `verdict`, `known_limits` | `pass`, `fail`, `inconclusive` plus disclosure |

A report without raw artifact hash or complete environment metadata cannot support a capacity claim.

## Existing Data Migration

1. Create a `legacy` tenant and memberships from current users; add nullable `tenant_id` to existing tables.
2. Backfill in restartable batches, recording table, cursor, source/target counts, checksum and failures.
3. Verify ownership and references before adding non-null, tenant-aware foreign keys and composite unique constraints.
4. Convert current conversations/messages/memory/knowledge/eval records without changing their evidence or memory authority semantics.
5. Copy local files to versioned object storage; compare byte count and SHA-256 before assigning `ObjectVersion`.
6. Build vector manifests for immutable document versions in Milvus; compare expected chunks and tenant filters before switching active retrieval.
7. Convert legacy drafts to `MaterialVersion`/`ChangeSet` compatibility projections; after authority switches, new writes only use the new model.
8. Contract/remove old columns and paths only after one release window of zero observed use and a verified recovery snapshot.

Every destructive step requires a pre-recorded recovery point. Rollback normally deploys the prior application against additive schema; it does not reverse destructive migrations.

## Cross-Entity Invariants

1. `tenant_id` must match across every relationship; repository APIs require it explicitly.
2. A run can resume only through its authorized `GraphCheckpointBinding`.
3. A policy conclusion or formal material draft requires immutable `EvidenceReference` rows from the current run unless the outcome is `insufficient_evidence`.
4. A `SubmissionJob` requires an unexpired approved digest, current RBAC and unchanged source/destination versions.
5. Exactly one authority exists for each concern: PostgreSQL business state, Milvus vectors, object storage bytes; adapters do not become a second authority.
6. Every retry is bounded and idempotent; every long-running item reaches terminal or recoverable state.
7. Physical deletion is complete only when reconciliation confirms metadata, vectors and all object versions are absent.
8. Formal policy originals never become writable workspace outputs; all edits create a new version.
9. Agent-generated drafts are excluded from formal retrieval until a separately authorized publish workflow completes.
10. Every critical state transition emits an audit event correlated by `run_id`.

