# Quickstart Validation Guide: 企业级智能体重构

**Purpose**: 提供可运行的端到端验收顺序；不是部署教程或实现清单。  
**Contracts**: [OpenAPI](contracts/openapi.yaml) · [Internal contracts](contracts/internal-contracts.md) · [Data model](data-model.md)

## Prerequisites

- Python 3.11+ 与项目 `policyflow` conda 环境；Node/TypeScript 与 Electron 构建工具。
- Docker/Kubernetes 测试环境，包含 PostgreSQL、PgBouncer、RabbitMQ、Redis、Milvus、MinIO/S3、OpenTelemetry Collector 和 gVisor RuntimeClass。
- 两个隔离租户：每个租户至少有 employee、approver、admin；含相似政策、同名文件和独立配额。
- 可重复初始化的知识语料、报销附件和恶意/边界文件 fixture。
- 明确 `status=mock` 的提交连接器，以及单独受控的真实 Claude provider 测试凭据。
- Locust worker 主机与被测服务分离；记录硬件、拓扑、commit 和数据规模。

> 当前仓库仍处于迁移前状态。以下命令名描述目标验证入口；各阶段实现任务必须让对应命令真实可运行，不能用占位成功代替。

## Common Setup

```bash
conda activate policyflow
```

```bash
pip install -e ".[dev]"
```

```bash
alembic upgrade head
```

```bash
pytest -q tests/contract tests/unit
```

Expected: migrations complete once outside API startup; API/OpenAPI, state machines, tenant query rules、证据门控、幂等、审批失效和路径规范化单测全部通过。

## Stage Gates

### 1. Capacity Baseline

```bash
locust -f tests/load/locustfile.py --headless --profile smoke --html artifacts/load/smoke.html --csv artifacts/load/smoke
```

Then run profiles `load`, `stress`, `spike`, `soak`, `sse`, `file-workflow`, `tenant-isolation` against the legacy baseline using deterministic mock LLM.

Expected:

- 每个运行保存原始 CSV/日志、环境 manifest 和 SHA-256。
- mock LLM 容量报告与 real-provider latency/rate-limit 报告完全分开。
- 本阶段可以不达 SC-001–SC-005，但必须产生可复现瓶颈证据。

### 2. Stateless API + PostgreSQL

```bash
pytest -q tests/integration/test_postgres_migrations.py tests/integration/test_multi_instance.py tests/security/test_tenant_isolation.py
```

Expected:

- 两个 API 实例经同一负载均衡可连续登录、聊天、读记忆/知识和运行 Eval。
- 重启任一实例不丢权威状态；生产 profile 拒绝 SQLite。
- 所有资源读取在数据库层带 tenant 谓词，RLS 纵深测试无跨租户结果。
- 源/目标记录数、外键、唯一约束和 legacy tenant 回填校验一致。

### 3. Shared LangGraph

```bash
pytest -q tests/contract/test_graph_state.py tests/integration/test_graph_entrypoint_parity.py tests/eval/test_evidence_gate_parity.py
```

Expected: 固定输入、权限、知识版本下，Chat、stream 和 Eval 的 `supported`/`insufficient_evidence` 结论一致率 100%；工具次数和总时限受控。影子比较关闭工具、记忆写回、文件写入和连接器。

### 4. Durable Checkpoints, Jobs and Quotas

```bash
pytest -q tests/recovery/test_run_restart.py tests/recovery/test_job_redelivery.py tests/integration/test_sse_resume.py tests/integration/test_quota_admission.py
```

Expected:

- 在运行、等待批准和 worker 处理中分别终止 API/worker，100% 测试任务恢复到正确状态。
- 重复队列投递只有一次状态变化；过期租约可安全重领。
- SSE 心跳、`Last-Event-ID`、取消、背压与断连生效，断开资源 30 秒内释放。
- 有界队列饱和返回 429/503 + `Retry-After`，不产生隐藏无限积压。

### 5. Milvus + Object Storage

```bash
pytest -q tests/integration/test_milvus_versions.py tests/integration/test_object_versions.py tests/recovery/test_cross_store_saga.py tests/reconciliation
```

Expected:

- Milvus 查询在 ANN 前过滤 tenant、KB、active document/embedding version 和 retrievable。
- 预置 orphan vector/object、missing chunk 和 version drift 检出率 100%。
- 更新期间不返回旧版本；Milvus 故障产生 `RETRIEVAL_UNAVAILABLE`，图以证据不足收束。
- 物理删除移除 PostgreSQL 引用、向量、所有对象版本/delete markers；失败保留可恢复状态。

### 6. File Sandbox + RBAC

```bash
pytest -q tests/security/test_workspace_escape.py tests/security/test_malicious_uploads.py tests/security/test_approval_authorization.py tests/recovery/test_submission_idempotency.py
```

Run the isolated sandbox suite inside the same RuntimeClass and policies used by the target environment.

Expected:

- `..`、绝对路径、Unicode/设备名、symlink、junction/shortcut、mount escape、TOCTOU、压缩炸弹和资源耗尽均被拒绝或隔离。
- 沙箱无任意网络、主机凭据、service account、host path 或 shell。
- 未批准、审批后内容变化、权限撤销和跨租户均产生 0 泄露/副作用。
- 重复点击、客户端重试、worker 恢复最多一次 connector side effect；未知结果先核对再重试。

### 7. Secure Electron Shell

```bash
npm --prefix frontend run test:electron
```

Expected:

- renderer 无 Node、任意文件、token 或 raw IPC 能力；非法 schema/origin 请求被拒绝。
- CSP、导航拦截、系统浏览器外链和 OS credential storage 测试通过。
- renderer 崩溃不会批准或提交任何动作；服务端运行保持权威状态。

### 8. Redesigned Desktop Workflows

```bash
npm --prefix frontend run test:electron:e2e
```

At common small, medium and large desktop window sizes, execute with keyboard only and automated accessibility checks:

1. Chat → evidence → answer/refusal.
2. Knowledge import/version/status.
3. Memory view/delete without policy-authority leakage.
4. Select materials → draft → tree/preview/version/diff → approve/reject.
5. Offline, permission denied, recoverable failure and conflict recovery.

Expected: 无遮挡或不可达关键操作；焦点可见且恢复正确；loading/empty/error/offline/recovery/permission states 有明确下一步。单独执行目标员工可用性研究，达到 SC-010–SC-011 后才算通过。

### 9. Comprehensive Acceptance

```bash
pytest -q tests/contract tests/unit tests/integration tests/security tests/recovery tests/migration tests/reconciliation
```

```bash
npm --prefix frontend run test
```

```bash
npm --prefix frontend run test:electron:e2e
```

```bash
locust -f tests/load/locustfile.py --headless --profile acceptance --expect-workers 4 --html artifacts/load/acceptance.html --csv artifacts/load/acceptance
```

Expected final gates:

- ≥1,000 active sessions，server failure <1%。
- 1,000 stable SSE；断开/取消资源 30 秒内清理且无增长趋势。
- non-LLM sustained ≥200 RPS，p95 <500 ms；首阶段事件 p95 <1 秒。
- 30-minute soak 无持续资源增长。
- tenant/越权/逃逸/未批准泄露和副作用全部为 0。
- 等待审批和可恢复任务重启恢复率 100%。
- Chat/stream/Eval evidence-gate parity 100%。
- cross-store seeded fault detection 100%。
- 用户、知识、会话、消息、记忆、草稿和 Eval 数据迁移核对 100%。

## Real Provider Validation

Run only after explicit test budget and credentials are configured; never mix these results into system capacity acceptance.

```bash
locust -f tests/load/real_provider.py --headless --profile provider-latency --html artifacts/provider/report.html --csv artifacts/provider/results
```

Expected: report provider/model, request/token quotas, prompt/output sizes, streaming latency, retry/429 behavior, token use and cost. SDK retries remain bounded by graph deadline; only retryable errors retry and `Retry-After` is respected.

## Failure Injection

Use Toxiproxy or equivalent controlled faults while running the relevant integration suite:

- PostgreSQL connection loss during state transition.
- RabbitMQ redelivery and worker death after side effect acknowledgement uncertainty.
- Redis loss during quota lease/SSE replay.
- Milvus unavailable or delayed activation.
- S3 upload/delete partial failure.
- Sandbox timeout/OOM/output corruption.
- Connector timeout with unknown external outcome.

Expected: each run/job reaches terminal or explicitly recoverable state; no silent fallback to stale retrieval, local files or unapproved submission.

## Evidence to Retain

For every gate, retain:

- commit SHA, dependency lock and container/image digests;
- topology, replica counts, pool/queue limits and hardware;
- seeded dataset/version/tenant manifest;
- raw test output with hash and generated summary;
- p50/p95/p99, throughput, errors, disconnects, queue depth and resource curves;
- deterministic mock version or real-provider mode/model;
- failed/unrun checks and known limits.

A generated summary without raw artifacts and environment metadata is not release evidence. Old web routes, legacy pipeline and local production stores may be removed only after all relevant parity, migration and acceptance evidence is attached to the release.

