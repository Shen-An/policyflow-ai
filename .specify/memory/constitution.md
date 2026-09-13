<!--
Sync Impact Report
- Version change: unratified scaffold → 2.1.0
- Modified principles: template placeholders → twelve enforceable enterprise refactoring principles
- Added sections: enterprise technology and storage boundaries; delivery workflow and quality gates
- Removed sections: none; placeholder sections were concretized
- Deferred items: none
- Review note: remove this temporary report before committing the constitution
-->

# PolicyFlow AI Constitution

## Core Principles

### I. Enterprise Scalability Is a Measured Requirement

FastAPI services MUST be stateless and horizontally scalable. Sessions, LangGraph state,
task progress, and rate-limit state MUST NOT live only in process memory. Production MUST
use PostgreSQL with bounded connection pools; long-running parsing, indexing, file, and MCP
work MUST run as background jobs. SSE MUST support heartbeat, disconnect detection,
cancellation, timeout, backpressure, and cleanup. LLM calls MUST have tenant and user quotas,
concurrency limits, bounded queues, and explicit overload responses. Writes MUST be
idempotent. This principle exists because adding workers alone does not create a reliable
high-concurrency system.

### II. Performance Claims Require Reproducible Load Tests

Locust MUST be the default API and concurrency load-testing tool. Every release claiming
enterprise capacity MUST cover smoke, load, stress, spike, soak, SSE long-connection, file
workflow, and tenant-isolation scenarios. System-only tests MUST use a deterministic mock LLM;
separate controlled tests MUST measure real-provider latency and throttling, and the two result
sets MUST never be conflated. Until a capacity plan replaces it, the acceptance baseline is
1,000 concurrent sessions, 1,000 stable SSE connections, 200 RPS for non-LLM APIs, p95 below
500 ms for non-LLM APIs, first SSE stage event p95 below 1 second excluding generation,
server error rate below 1%, and a 30-minute soak without sustained resource growth. Reports
MUST record commit, topology, hardware, data volume, workload, p50/p95/p99, throughput,
errors, disconnects, queue depth, resource usage, LLM mode, bottleneck evidence, and before /
after comparisons.

### III. LangGraph Is the Single Honest Orchestrator

Chat, streaming Chat, Eval, and desktop workflows MUST invoke one shared LangGraph core graph.
The graph MUST explicitly model validation, memory load, query rewrite, retrieval, rerank,
evidence gating, tool or sandbox execution, human approval, answer generation, writeback, and
audit. State MUST use typed schemas; every node MUST define inputs, outputs, failures,
timeouts, and retries. Tool loops MUST have bounded iterations and total runtime. Durable
checkpoints MUST bind tenant, user, and thread identity and support recovery after restart.
LangGraph MUST NOT be described as a security sandbox, and nodes MUST NOT be split or traces
fabricated merely to simulate multi-agent behavior.

### IV. File Actions Require a Real Sandbox and Human Approval

Every file task MUST run in an isolated workspace bound to tenant, user, session, and task.
The Agent MUST access only files explicitly selected or uploaded for that task; normalized
paths, links, shortcuts, and mount points MUST be checked against workspace escape. Formal
policy originals MUST be read-only, with edits made as new versions. Network, arbitrary shell,
and host credentials MUST be unavailable by default. File type, size, total space, CPU, memory,
and runtime MUST be limited, and uploads MUST be scanned for malicious content and archive
bombs. Script execution, when justified, MUST use an allowlist plus process, container, or OS
isolation; path validation alone is not a sandbox. Overwrite, delete, publish, submit, upload,
external send, and workspace export MUST pause for explicit approval showing destination,
files, diff, and side effects. Agent authority MUST never exceed the initiating user's RBAC.

### V. Storage Responsibilities Are Strictly Separated

Production MUST use PostgreSQL for authoritative business data and metadata, Milvus as the
only vector retrieval backend, and MinIO or an S3-compatible enterprise object store for
original and generated files. SQLite, local directories, JSON, Markdown, Pickle, FAISS files,
and process-local indexes MAY be used only for development or isolated tests. Original PDF,
Word, image, and attachment bytes MUST NOT be stored in Milvus. Milvus records MUST identify
tenant, knowledge base, document, document version, chunk, embedding model, and embedding
version; every query MUST enforce tenant filtering before retrieval. Updates MUST create a new
version and rebuild vectors. Physical deletion MUST reconcile PostgreSQL metadata, Milvus
vectors, and object-store objects. Cross-store work MUST be idempotent, recoverable, and
audited; the system MUST NOT claim native distributed transactions. Scheduled reconciliation
MUST detect orphan vectors, orphan objects, missing chunks, and version drift. Milvus failure
MUST surface retrieval unavailability rather than silently generate without evidence.

### VI. The Desktop Client Is the Product Surface

The public end-user web client MAY be discarded after capability parity; Electron + React +
TypeScript MUST be the sole target desktop architecture. React remains the renderer, Electron
the desktop container, a minimal preload API the capability boundary, and FastAPI/LangGraph
the centralized enterprise service. The project MUST NOT maintain Electron and Tauri clients
in parallel without a separately approved architecture decision. Electron MUST enable context
isolation, disable Node integration in renderers, validate all IPC schemas, enforce CSP, avoid
unapproved remote pages, open external links in the system browser, store tokens in OS secure
credential storage, and use signed packages and updates. A renderer crash MUST NOT allow an
unapproved side effect to continue.

### VII. UI Quality Is an Acceptance Contract

The old web UI MUST NOT simply be wrapped in Electron. The desktop application MUST use a
coherent design system for color, type, spacing, radius, elevation, and interaction states.
Chat, sandbox workspace, approval, knowledge-base, and memory workflows MUST share consistent
navigation and hierarchy. Agent progress MUST use a compact staged timeline with details
collapsed by default. File workflows MUST expose tree, preview, diff, version, and approval
state. Loading, empty, error, offline, recovery, and permission-denied states MUST be designed.
Keyboard use, focus visibility, contrast, and semantic accessibility MUST be tested. Core flows
MUST be exercised in the real Electron application at common window sizes; a polished static
screen is not feature verification.

### VIII. Tenant Isolation and Least Privilege Are Non-Negotiable

Users, roles, conversations, messages, memories, documents, chunks, vectors, checkpoints,
sandboxes, tasks, approvals, caches, and audit events MUST carry and validate tenant ownership.
Server-side RBAC MUST authorize every read and side effect; client visibility is never an
authorization control. Tenant filtering MUST occur in database and Milvus queries, not after
retrieval. Cache namespaces, object keys, graph threads, and workspaces MUST be isolated.
Cross-tenant administration MUST be disabled by default, explicitly granted, and independently
audited. Automated security and load tests MUST prove tenant isolation.

### IX. Evidence Has Authority; Memory Does Not

Retrieval remains a service and MUST NOT be renamed as an Agent to inflate architecture claims.
Policy conclusions MUST cite reliable evidence from the current RAG run. Missing or irrelevant
evidence MUST produce `insufficient_evidence`; unrelated results MUST NOT justify an answer.
Skills MUST bind to evidence. Local lexical fusion MUST NOT be described as a cross-encoder.
Memory MAY help interpret context and preferences but MUST NOT override or rewrite policy
evidence. Agent-generated files remain drafts until approved and MUST NOT enter the formal
knowledge base beforehand. Template and checklist requests MAY synthesize a fillable structure
only from supported evidence.

### X. Code Requires Accurate, Purposeful Commentary

All new and refactored code MUST include documentation proportional to its complexity. Python
public modules, core classes, and public functions MUST have concise docstrings; TypeScript
public components, hooks, IPC contracts, and shared types MUST document their responsibilities
or constraints. LangGraph state transitions, tenant and sandbox boundaries, Milvus schema and
index choices, cross-store consistency, idempotency, retry, timeout, compensation, SSE cleanup,
non-obvious performance work, temporary compatibility logic, and side-effecting tools MUST
explain why the design exists and what must remain true. Comments MUST NOT narrate obvious
statements or substitute for clear names and tests. TODOs MUST include reason, target state, and
a tracking reference. Changed behavior MUST update related comments; stale comments are defects.

### XI. Observability and Recovery Are Release Requirements

A single `run_id` MUST correlate API requests, SSE streams, LangGraph threads, retrieval,
model calls, tools, MCP, sandbox jobs, approvals, file submissions, retries, and errors.
Telemetry MUST expose API latency and errors, RPS, active SSE connections, database pools,
queue depth, LLM concurrency and tokens, graph node latency and failures, Milvus query and
index health, sandbox starts and limits, file outcomes, and per-tenant resource use. Every
long-running or side-effecting task MUST reach an explicit terminal or recoverable state.
Retries MUST be bounded and idempotent, and the system MUST never silently resubmit user
materials.

### XII. Incremental Migration and Verification Define Done

Refactoring MUST proceed through independently runnable stages: baseline load tests; stateless
API plus PostgreSQL; shared LangGraph; durable checkpoints, quotas, and background jobs;
Milvus and object storage; sandbox and RBAC; secure Electron shell; redesigned workflows; then
functional, security, performance, and stability acceptance. The project MUST NOT rewrite all
layers before integration or maintain permanent dual orchestration paths. Legacy web removal
requires desktop parity and preservation of user and knowledge-base data. Completion requires
passing automated unit, integration, tenant-isolation, cross-store reconciliation, sandbox,
Electron E2E, and Locust load/stress/spike/soak tests. Any unrun validation MUST be reported;
“theoretically works” is not evidence.

## Enterprise Technology and Storage Boundaries

- Backend: Python 3.11+, FastAPI, SQLModel / SQLAlchemy.
- Production transaction store: PostgreSQL; SQLite is development-only.
- Production vector store: Milvus; collection, index, consistency, replica, and partition
  choices MUST be justified by retrieval and load-test evidence.
- File store: MinIO or S3-compatible enterprise object storage with versioning and lifecycle
  policy.
- Agent orchestration: LangGraph with persistent checkpoints and human-in-the-loop interrupts.
- Client: Electron + React + TypeScript with a minimal capability broker.
- Load testing: Locust for APIs and concurrency; Electron E2E tooling for user workflows.
- Existing retrieval, Skill, Tool, and MCP honesty constraints remain binding.
- New infrastructure or framework dependencies require a measured performance, reliability,
  security, or operability need; popularity alone is not sufficient.

## Delivery Workflow and Quality Gates

Every specification and plan MUST identify affected principles, contracts, migration risks,
security boundaries, load scenarios, and measurable acceptance criteria. Contract changes to
APIs, SSE events, Graph State, storage schemas, IPC, evaluation output, or permissions MUST be
explicit. Destructive or externally visible actions require user confirmation and an audit
record. Data migrations require validation and a recovery strategy.

A refactoring increment is complete only when all applicable gates pass:

1. FastAPI runs statelessly across multiple instances against PostgreSQL.
2. Chat, streaming Chat, Eval, and tools use the same LangGraph core path.
3. Production retrieval uses tenant-filtered Milvus and never returns stale document versions.
4. Original and generated files use object storage, not host-local production paths.
5. Cross-store idempotency and reconciliation leave no orphan vectors or objects after update
   and physical deletion.
6. Sandbox tests cover path escape, symlinks, resource limits, malicious uploads, unauthorized
   actions, and approval cancellation.
7. Electron renderers have no direct Node or unrestricted file access, and core workflows pass
   real desktop E2E tests.
8. Locust scenarios meet the approved capacity baseline and preserve their raw results and
   environment metadata.
9. Metrics, logs, traces, and audit events cover every critical path.
10. Core interfaces, complex behavior, security boundaries, and non-obvious choices have
    accurate comments and tests.
11. Documentation, demonstrations, and résumé claims disclose mock connectors, local rerank,
    sample size, load environment, and known limits.

## Governance

This constitution supersedes feature specifications, implementation convenience, framework
marketing, and demonstration pressure. Every Spec, Plan, task set, code review, and release
review MUST demonstrate compliance with the applicable principles. Existing conflicts MUST be
recorded as migration debt with an owner and acceptance test; they MUST NOT be silently
normalized.

Amendments require a written rationale, affected principles, impact on architecture and
migration, security and performance risks, and corresponding validation. Approved amendments
MUST update this document, its semantic version, and amendment date before dependent work is
accepted. Exceptions require a documented scope, reason, risk, accountable owner, compensating
controls, and expiry date; no exception is permanent by default.

Versioning follows semantic versioning: MAJOR for incompatible changes to client shape, runtime
model, security boundary, or governing principles; MINOR for a new principle or material
expansion; PATCH for non-semantic clarification. Compliance is reviewed at planning, pull
request, and release gates. Claims such as “LangGraph provides a sandbox,” “Electron may call
anything locally,” or “it worked on one machine” never waive these controls.

**Version**: 2.1.0 | **Ratified**: 2026-09-12 | **Last Amended**: 2026-09-12
