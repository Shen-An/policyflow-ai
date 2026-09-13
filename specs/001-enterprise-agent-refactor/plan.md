# Implementation Plan: 企业级智能体重构

**Branch**: `master` | **Feature ID**: `001-enterprise-agent-refactor` | **Date**: 2026-09-12 | **Spec**: [spec.md](spec.md)

**Input**: `specs/001-enterprise-agent-refactor/spec.md`，质量清单见 [checklists/requirements.md](checklists/requirements.md)。

## Summary

以九个可独立运行、可验证、可恢复的阶段，将当前 FastAPI + SQLite + 本地文件/检索 + React Web 应用迁移为企业级 PolicyFlow：PostgreSQL 权威业务状态、单一类型化 LangGraph、RabbitMQ/Celery 持久任务、Redis 有界协调、Milvus 租户过滤检索、版本化 MinIO/S3 文件、gVisor 文件沙箱，以及安全 Electron 桌面端。迁移保留现有政策问答、证据门控、四层非权威记忆、Skill/Tool/MCP 诚实边界和检索评估，并以真实原始测试产物证明容量、安全、恢复和数据一致性。

首个纵向切片是“选择报销材料 → 证据支持的草稿 → 差异预览 → 人工审批 → 幂等提交”。系统容量使用确定性 mock LLM 测量；真实 Claude provider 的延迟、限流、token 和成本单独报告，不混入系统容量结论。

## Technical Context

**Language/Version**: Python 3.11+；TypeScript 6 / React 19；Electron 目标运行时按实现时受支持版本锁定

**Primary Dependencies**: FastAPI、SQLModel / SQLAlchemy 2、psycopg 3、Alembic、LangGraph + `langgraph-checkpoint-postgres`、Celery 5、RabbitMQ quorum queues、Redis、Milvus、MinIO/S3 SDK、Anthropic Python SDK、`sse-starlette`、OpenTelemetry、Electron、WebdriverIO Electron Service、Locust、Testcontainers、Toxiproxy

**Storage**: PostgreSQL（业务、状态、审计、幂等权威）；Milvus（唯一生产向量检索）；版本化 MinIO/S3（原始与生成文件）；Redis（可丢弃缓存、配额租约、短期 SSE 重放）；SQLite/本地目录仅限开发或隔离测试

**Testing**: pytest 单元/契约/集成/安全/恢复/迁移/核对；Locust 分布式容量测试；WebdriverIO Electron E2E；自动无障碍检查；Testcontainers；Toxiproxy 故障注入

**Target Platform**: Kubernetes Linux 服务与 worker；gVisor RuntimeClass 沙箱 Job；Windows/macOS 企业 Electron 桌面包

**Project Type**: FastAPI 服务 + 后台 worker + Electron/React 桌面客户端

**Performance Goals**: ≥1,000 活跃会话；1,000 稳定 SSE；non-LLM sustained ≥200 RPS；non-LLM p95 <500 ms；首阶段事件 p95 <1 秒；server failure <1%；30 分钟 soak 无持续资源增长

**Constraints**: 单一 LangGraph；证据不足 fail closed；内存/Redis/队列均非业务权威；租户谓词在数据库与 Milvus 查询内执行；所有副作用审批、重授权、幂等；沙箱无任意网络/主机路径/凭据/shell；生产不得依赖 SQLite 或本地文件

**Scale/Scope**: 九阶段全栈迁移，覆盖 Chat、stream、Eval、知识、记忆、文件工作区、审批、提交、管理、审计、容量与旧 UI 退出；移动端与全自动提交不在范围内

**Evidence-derived settings**: Pod/worker 数、连接池、队列上限、Redis 配额、Milvus 索引参数、沙箱并发和 LLM 配额不预先拍定；由基线、真实语料和故障测试标定并记录。

## Current Baseline and Target Gap

可复用基础包括 `AgentPipeline`/`TurnState`/`TurnBudget`、证据门控、query rewrite、四层记忆、Skill/Tool/MCP 注册、物理删除意图、检索 Eval、SSE 阶段事件、React 功能页面与现有 pytest/浏览器测试。这些语义要通过契约测试迁移，不能以框架重写为理由退化。

必须替换的生产边界包括进程内队列/锁/信号量/缓存、FastAPI `BackgroundTasks`、SQLite 权威状态、host-local 文件和 LightRAG workspace、可变文档引用、非持久审批/草稿流程、无重放与背压的 SSE，以及缺失 main/preload/IPC 安全边界的浏览器 SPA。现有 `AgentPipeline` 作为限期兼容入口调用共享图，不能长期成为第二套编排。

## Constitution Check — Pre-Design Gate

| Principle | Gate result | Planned enforcement |
|---|---|---|
| I. Measured scalability | PASS | 无状态 API、外置权威状态、有界池/队列/配额、幂等写入 |
| II. Reproducible load tests | PASS | 分布式 Locust、原始产物、mock/real provider 分离 |
| III. Single LangGraph | PASS | Chat/stream/Eval/file 共用类型化核心图和 PostgreSQL checkpoint |
| IV. Sandbox + approval | PASS | gVisor Job、受控输入、资源限制、action digest、HITL interrupt |
| V. Storage separation | PASS | PostgreSQL/Milvus/S3 单一职责，saga + reconciliation |
| VI. Electron product surface | PASS | 安全 main/preload/renderer，能力对等后退出旧 Web |
| VII. UI acceptance | PASS | 统一设计系统、真实 Electron E2E、可访问性与用户研究 |
| VIII. Tenant isolation | PASS | 全实体 tenant ownership、服务端 RBAC、查询内过滤、RLS 纵深防御 |
| IX. Evidence authority | PASS | 当前运行不可变证据；记忆不满足 evidence gate；检索失败关闭 |
| X. Purposeful commentary | PASS | 公共契约和非显然安全/一致性不变量随实现记录并测试 |
| XI. Observability/recovery | PASS | `run_id` 全链路、OTel、显式终态/可恢复态、边界重试 |
| XII. Incremental verification | PASS | 严格九阶段；每阶段独立入口、退出门和回退/恢复路径 |

无宪法例外；Phase 0 研究可开始。现有不合规实现视为待迁移基线，不被当作已批准例外。

## Project Structure

### Documentation (this feature)

```text
specs/001-enterprise-agent-refactor/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   ├── openapi.yaml
│   └── internal-contracts.md
└── tasks.md                 # 仅由 /speckit-tasks 后续生成
```

### Target Source Layout

```text
backend/app/
├── api/                     # v2 run/material/approval API 与 legacy adapters
├── auth/                    # principal、tenant membership、RBAC
├── db/                      # SQLModel/SQLAlchemy 模型、session、repositories
├── graph/                   # 唯一 LangGraph state、nodes、entrypoints
├── jobs/                    # durable job/outbox/Celery consumers
├── retrieval/               # Milvus service、index/version activation
├── storage/                 # 版本化 object-store adapter 与 saga
├── sandbox/                 # allowlisted processor 与 K8s Job controller
├── approvals/               # digest、decision、submission/reconciliation
├── streaming/               # SSE durable milestones、Redis replay、cleanup
├── observability/           # OTel、metrics、audit/redaction
├── services/                # 业务服务；保留 memory/query rewrite 语义
└── integrations/            # Claude provider 与明确标记 mock 的 connectors

frontend/
├── electron/
│   ├── main/                # 窗口、认证代理、导航、安全存储
│   └── preload/             # 最小、schema-validated capability API
├── src/
│   ├── app/                 # desktop shell、router、providers
│   ├── design-system/       # tokens、primitives、states
│   ├── features/            # chat/knowledge/memory/workspace/approval/admin
│   └── services/            # typed API/event clients; no raw IPC
└── tests/electron/          # security、workflow、accessibility E2E

migrations/                  # Alembic expand/backfill/enforce/contract
infra/                       # K8s、RabbitMQ、Redis、Milvus、S3、OTel 配置

tests/
├── contract/
├── unit/
├── integration/
├── security/
├── recovery/
├── migration/
├── reconciliation/
├── eval/
└── load/
```

**Structure Decision**: 保留现有 `backend/app` 与 `frontend` 顶层，按企业边界增量拆分；不新建第二个业务服务或第二套 agent 项目。数据库迁移、基础设施和跨层测试提升为根级目录，以便每个阶段单独部署和验收。

## Design Artifacts

- [research.md](research.md)：技术选型、替代方案和约束。
- [data-model.md](data-model.md)：实体、状态机、迁移和跨实体不变量。
- [contracts/openapi.yaml](contracts/openapi.yaml)：统一 run、SSE、审批和材料上传的公共 v2 契约。
- [contracts/internal-contracts.md](contracts/internal-contracts.md)：principal、Graph State、jobs、retrieval、object store、sandbox、connector、audit 和 error 边界。
- [quickstart.md](quickstart.md)：九阶段目标验证入口和发布证据要求。

## Incremental Delivery Plan

### Stage 1 — Capacity Baseline

**Purpose**: 在改变架构前建立可复现瓶颈证据，不宣称旧系统已达目标。

**Changes**:
- 建立 Locust profiles：smoke、load、stress、spike、30-minute soak、SSE、file workflow、saturation、tenant isolation。
- 实现版本化 deterministic mock LLM，固定输出、工具调用、延迟和错误脚本。
- 记录 commit、依赖、硬件、拓扑、数据规模、原始 CSV/日志与 SHA-256。
- 真实 provider 只做受预算控制的独立 latency/rate-limit/token/cost 测试。

**Exit gate**: 所有 profile 可重复运行，瓶颈和生成器资源被记录；mock 与 real-provider 报告物理分开。

### Stage 2 — Stateless API + PostgreSQL

**Purpose**: 先消除多实例下的状态丢失和 SQLite 单机权威。

**Changes**:
- 增加 Tenant/User/Role/Grant、run、idempotency、audit 等 additive schema 和 Alembic 管线。
- 使用 SQLAlchemy 2 async + psycopg 3；每工作单元独立 session/事务；连接预算有上限和等待超时。
- principal 从认证 membership 派生；repository 必须显式接收 tenant；PostgreSQL RLS 作纵深防御。
- 将会话、记忆、知识、Eval 等现有数据回填到 `legacy` tenant，批量记录 count/checksum/cursor。
- 移除 API correctness 对进程锁、队列、缓存和 startup migrations 的依赖。

**Exit gate**: 两个 API 实例共享 PostgreSQL 连续运行；任一实例重启不丢状态；生产 profile 拒绝 SQLite；迁移核对 100%。

### Stage 3 — Shared LangGraph

**Purpose**: 将所有入口收敛到一个可恢复、可审计的决策路径。

**Changes**:
- 定义版本化 `AgentRunState` 和节点契约：validate、memory_load、rewrite、retrieve、rerank、evidence_gate、plan/tool、sandbox、approval interrupt、generate、writeback、finalize。
- 通过 `GraphCheckpointBinding` 授权 opaque thread；使用 PostgreSQL saver。
- Chat、stream、Eval 和 file workflow 共用 invoke/stream/resume service。
- 保留 query rewrite、四层非权威记忆、证据门控、Skill/Tool/MCP 诚实语义。
- 旧 routes 和 `AgentPipeline` 仅做 v2 graph adapter；影子比较禁用工具、写回、文件和 connector side effects。

**Exit gate**: 固定输入/权限/知识版本下 Chat、stream、Eval evidence-gate parity 100%；工具次数与总 deadline 受控。

### Stage 4 — Durable Checkpoints, Jobs and Quotas

**Purpose**: 让长任务、SSE 和过载行为跨实例、重启可恢复。

**Changes**:
- PostgreSQL `DurableJob` + transactional outbox 为权威；Celery/RabbitMQ quorum message 仅唤醒 consumer。
- lease token、expected version、publisher confirm、manual/late ack、DLQ、有限重试、jitter、soft/hard timeout、`prefetch=1`。
- Redis Lua token bucket 与租期 semaphore 实施 tenant/user/global admission；策略和最终 usage 写 PostgreSQL。
- SSE 使用 heartbeat、send timeout、有界 AnyIO channel、Redis Streams 重放和 durable snapshot fallback。
- 取消在 bounded steps 间协作检查；队列饱和返回 429/503 + `Retry-After`。

**Exit gate**: API/worker kill、消息重复、lease 过期、SSE reconnect/cancel 和 Redis 短时故障测试均达到显式正确状态且无重复转换。

### Stage 5 — Milvus + Object Storage

**Purpose**: 建立检索内容和文件字节的生产权威边界与版本一致性。

**Changes**:
- 共享 Milvus collection 首期以 `tenant_id` 为 partition key；每行包含 KB、document/material version、chunk、embedding version、`retrievable`。
- ANN 前强制 tenant + allowed KB + active immutable version + embedding version 过滤；不可用返回 `RETRIEVAL_UNAVAILABLE`。
- 新向量先 stage/verify，再 compare-and-set active version；不返回旧版本或半成品。
- MinIO/S3 开启版本控制；客户端仅使用 material/version ID 或短期受限上传能力。
- PostgreSQL 保存 opaque key、provider VersionId、SHA-256、size、media type 和 lifecycle。
- 以 outbox + saga + scheduled reconciliation 处理跨存储更新和物理删除。

**Exit gate**: 更新期间无 stale retrieval；预置 missing/orphan/drift 全部检出；物理删除移除所有引用、向量、对象版本和 delete markers。

### Stage 6 — File Sandbox + RBAC

**Purpose**: 交付安全文件纵向切片，不把路径校验或 LangGraph 冒充沙箱。

**Changes**:
- `TaskWorkspace` 仅引用用户明确选择的 immutable material versions；正式政策输入只读。
- 每任务启动 gVisor Kubernetes Job：non-root、read-only root、drop capabilities、seccomp、无 service account/hostPath/default egress。
- `processor_id` 服务端映射 signed allowlisted image + fixed argv；限制 CPU、内存、PID、时间、临时空间和输出。
- magic-byte/type/size/malware/archive-bomb 检查；拒绝 traversal、绝对路径、links、junctions、mount escape、设备名、异常编码和 TOCTOU。
- `ChangeSet` 与 approval action digest 绑定目标、exact versions/hashes、diff、side effects；任何变化失效。
- side effect 前 fresh RBAC + digest check；submission 唯一幂等键；unknown outcome 先 reconcile。

**Exit gate**: 所有越权、跨租户、逃逸、恶意上传和未批准测试为 0 泄露/副作用；重复触发最多一次业务动作。

### Stage 7 — Secure Electron Shell

**Purpose**: 先建立可信桌面能力边界，再迁移完整 UI。

**Changes**:
- 创建 Electron main/preload/renderer 架构；renderer `contextIsolation` + sandbox，关闭 Node integration。
- preload 仅暴露 operation-specific、schema-validated APIs；禁止 raw IPC、arbitrary file path/token access。
- main 代理认证 API/SSE；refresh token 使用 OS `safeStorage`；验证 sender origin。
- 严格 CSP、导航与新窗口拒绝、外链交给系统浏览器；准备签名 package/update 管线。
- renderer 销毁取消尚未形成服务端批准的 privileged request；服务端 run 仍为权威。

**Exit gate**: Electron security tests 证明 renderer 无 Node、任意文件、token 或 raw IPC；崩溃不会批准或提交动作。

### Stage 8 — Redesigned Desktop Workflows

**Purpose**: 不是包装旧网页，而是交付一致、可访问、可恢复的企业桌面体验。

**Changes**:
- 落地统一 color/type/spacing/radius/elevation/interaction tokens，遵守项目 soft mint + white floating cards 方向。
- 统一导航与 chat、knowledge、memory、workspace、approval、admin 信息层级。
- Agent progress 为安静的 compact staged timeline，详情默认折叠。
- 文件流程整合 tree、preview、version、diff、target、approval 与 submission result。
- 明确 loading/empty/error/offline/recovery/permission/conflict states 和下一步。
- 在 small/medium/large 常见窗口执行真实 Electron keyboard/focus/contrast/semantic E2E。

**Exit gate**: 自动 E2E/无障碍全部通过；目标员工完成率 ≥90%，5 分钟内无引导完成核心流程；清晰度/信心 ≥90% 评分 4/5+。

### Stage 9 — Comprehensive Acceptance and Legacy Exit

**Purpose**: 以证据完成权威切换，随后删除临时双路径。

**Changes**:
- 执行功能、contract、integration、security、recovery、migration、reconciliation、Electron E2E 和全部 Locust acceptance profiles。
- 核对 SC-001–SC-016 与原始 artifacts；披露任何未运行、失败或环境差距。
- 验证用户、知识、会话、消息、记忆、草稿、Eval、对象和向量迁移 100%。
- 观察一个 release window，确认 legacy adapter/columns/local paths 零使用并保留恢复点。
- 依次停写、停读、删除旧 Pipeline 业务路径、旧 Web surface 和本地生产 stores；更新架构、面试、演示与 résumé 边界。

**Exit gate**: 所有适用验收通过；无永久双主/双编排；旧路径删除前后均有迁移校验和可恢复快照。

## Compatibility and Removal Ledger

| Temporary compatibility | Owner area | Removal condition | Latest removal stage |
|---|---|---|---|
| Legacy Chat/Eval routes → v2 run adapter | API/Graph | 客户端切换且 parity 100%，一个 release window 零直接旧调用 | 9 |
| `AgentPipeline` facade → shared LangGraph | Graph | 所有入口只调用 graph service，影子结果通过 | 9 |
| SQLite development adapter | Data | 生产与迁移测试拒绝 SQLite；开发 fixtures 有替代 | 9（开发模式可保留） |
| Host-local material/workspace reader | Storage | 对象迁移 checksum 100%，生产访问为零 | 9 |
| LightRAG production workspace | Retrieval | Milvus version/tenant/reconciliation gates 通过 | 9 |
| Browser web shell | Desktop | Electron capability/data parity 与 SC-010–SC-012/016 通过 | 9 |
| Draft compatibility projection | Files | 新 MaterialVersion/ChangeSet 工作流覆盖全部写入 | 9 |

每项实现任务必须指定责任模块、遥测计数、删除测试和截止 stage；不得无限期续存。

## Contracts and Compatibility

- `POST /api/v2/runs` 统一 chat/eval/file_workflow；身份字段禁止出现在请求 body，来自 principal。
- `GET /runs/{id}/events` 定义有序 `RunEvent@2.0`、heartbeat、`Last-Event-ID` 和 snapshot fallback。
- approval endpoint 使用 expected version + SHA-256 action digest；响应“记录批准”不等于跳过执行前 fresh authorization。
- material upload 仅创建受 size/media/hash/purpose 约束的短期 transfer capability；客户端不选 object key。
- 内部所有 API 接收 `RequestPrincipal`；job mutation 需要 lease token + expected state/version。
- public/internal errors 使用稳定 code、retryable 和 server-controlled delay，不泄露租户存在性、路径、key、凭据或 raw provider payload。
- Eval 可以 deterministic 且禁用副作用，但不能绕过共享 evidence gate。

契约先于实现加入 contract tests；legacy 响应仅在显式 adapter 中映射，不在新核心中维护双 schema。

## Data Migration and Recovery

1. 所有 schema 先 additive；创建 `legacy` tenant 和 membership，旧表 `tenant_id` 先 nullable。
2. 以可重启 batch backfill，保存 cursor、source/target count、checksum 和 failures。
3. ownership/reference 核对通过后，添加 NOT NULL、tenant-aware FK/composite unique/RLS。
4. 文件复制到版本化 object storage 后逐对象核对 byte count + SHA-256，成功才设置 authority pointer。
5. Milvus manifest 先不可检索写入并核对 count/hash/filter，再 CAS 切 active version。
6. authority 切换后新写仅走新模型；禁止永久双写。
7. destructive contract step 前记录恢复点；常规 rollback 部署旧应用读取 additive schema，不反向执行破坏性 migration。
8. 物理删除失败保持 `deleting`/recoverable 状态，直到 PostgreSQL、Milvus 和所有对象版本核对为空。

## Security Boundaries

- **Identity**: tenant 来自已验证 membership，不信任 request body；approval execution 获取 fresh principal/authorization version。
- **Database**: repository 查询内 tenant predicate；RLS defense in depth；应用角色不拥有表、不具 `BYPASSRLS`。
- **Retrieval**: tenant/KB/version/retrievable 过滤在 ANN 前；memory 永远不满足政策 evidence gate。
- **Files**: API 只接受 material/version IDs；object keys、host paths 和 sandbox infrastructure refs 不对模型/client 暴露。
- **Sandbox**: 无 arbitrary image/URL/credential/network/shell；输出按 manifest/hash 校验并拒绝 links/异常名称。
- **Approval**: digest 绑定 exact action；permission/content/destination/evidence 改变即 invalidated；side effect 前重验。
- **Electron**: renderer 不持有 refresh token/Node/raw IPC；main 是最小 capability broker。
- **Telemetry**: redact credentials、file bodies、host paths、raw provider payload 和 unrestricted PII；tenant/user 不用作高基数 metric label。

## Claude and LLM Execution Policy

- 使用官方 Anthropic async Python SDK；模型与 provider 配置集中管理，不散落在 graph nodes。
- 复杂、当前模型支持的请求使用 adaptive thinking；长输入/输出使用 SDK streaming，并从完整 final message 读取结构化结果。
- provider 请求前取得 tenant/user/global concurrency lease；queue admission 有界，拒绝时返回明确重试信息。
- 解析 typed stop reason 和 SDK errors；仅 retryable connection/408/409/429/5xx 在 graph deadline 内有限退避，尊重 `Retry-After`。
- tool/connector side effects 不因模型重试重复；工具幂等键与 run/node attempt 绑定。
- deterministic mock 固定并版本化内容、工具调用、延迟和错误，不访问外部 provider。
- 真实 provider 测试必须先配置受控凭据和预算，单独记录 model、request/token quota、prompt/output size、stream latency、429、tokens 和 cost。
- 不采用 Managed Agents 作为核心：其托管 loop/sandbox 与本项目自建 LangGraph、企业存储和本地安全边界重叠。

## Validation Matrix

| Requirement / risk | Automated evidence | Release evidence |
|---|---|---|
| Shared decisions/evidence | graph-state contract + entrypoint parity + evidence-gate Eval | SC-013 100% |
| Restart/replay/idempotency | API/worker kill, RabbitMQ redelivery, lease expiry, duplicate request | SC-008/009 |
| Tenant isolation | DB RLS/repository, Milvus prefilter, cache/object/thread/workspace tests under load | SC-007/014 |
| Sandbox escape/malware | traversal/link/junction/mount/TOCTOU/archive bomb/resource suites in target RuntimeClass | SC-007 |
| Approval/submission | stale digest, revoked RBAC, crash, duplicate click, unknown outcome reconciliation | SC-008/009 |
| Cross-store consistency | seeded missing/orphan/drift plus update/delete saga faults | SC-015 |
| Electron boundary | preload schema/origin/CSP/navigation/token/node/renderer-crash tests | security gate |
| Desktop usability | real Electron E2E at common sizes + keyboard/a11y + moderated study | SC-010–012 |
| Capacity/stability | distributed Locust smoke/load/stress/spike/soak/SSE/file/isolation | SC-001–006 |
| Data preservation | row counts, FKs, unique constraints, hashes, version manifests, legacy read comparison | SC-016 |
| Evidence honesty | retrieval unavailable/off-topic/other-tenant/current-version tests | SC-013/014 |

Detailed runnable target commands and expected results are in [quickstart.md](quickstart.md). A generated summary without raw artifacts, environment manifest and hashes cannot satisfy a gate.

## Constitution Check — Post-Design Re-evaluation

| Principle | Post-design result | Evidence in design |
|---|---|---|
| I–II Scalability/load | PASS | Stages 1/2/4/9, bounded resources, reproducible artifact requirements |
| III Single orchestrator | PASS | One versioned graph, authorized checkpoint binding, legacy facade removal condition |
| IV Sandbox/HITL | PASS | gVisor policy, immutable inputs, digest approval, fresh RBAC, idempotent submission |
| V Storage separation | PASS | Explicit authority table, Milvus prefilter/versioning, S3 versions, saga/reconciliation |
| VI–VII Desktop/UI | PASS | Secure Electron boundary, non-wrapper redesign, real E2E and user success gates |
| VIII Tenant isolation | PASS | Principal propagation, tenant-aware FKs/queries/RLS, cross-store and load tests |
| IX Evidence authority | PASS | Current-run immutable evidence, memory exclusion, fail-closed retrieval |
| X Documentation | PASS | Public contracts and non-obvious invariants named as implementation deliverables |
| XI Observability/recovery | PASS | `run_id`, OTel, durable states, bounded retries, fault injection |
| XII Incremental migration | PASS | Nine independently gated stages, compatibility ledger, no permanent dual authority |

Phase 1 design introduces no constitutional violation or unresolved clarification. Capacity and infrastructure tuning values remain intentionally evidence-derived, not unspecified requirements.

## Complexity Tracking

No constitutional violations require justification.
