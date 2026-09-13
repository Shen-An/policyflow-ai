# Phase 0 Research: 企业级智能体重构

**Date**: 2026-09-12  
**Spec**: [spec.md](spec.md)  
**Constitution**: 2.1.0

本文记录实施计划采用的技术决策。所有生产能力均须通过后续阶段的测试门，不把选型本身当作容量、安全或可靠性证据。

## 1. 部署与无状态 API

- **Decision**: 企业参考部署采用 Kubernetes；每个 Pod 运行一个 Uvicorn worker，由入口负载均衡并水平扩缩。FastAPI 只保留请求内临时对象，运行、任务、审批、幂等和审计写入 PostgreSQL；Redis 只保存可丢弃缓存、分布式限流/并发租约及短期 SSE 重放事件。
- **Rationale**: 单 worker Pod 便于计算数据库连接、观测资源和优雅下线；持久状态外置后，多实例与重启不会改变业务语义。
- **Alternatives considered**: 单机多 worker 适合开发或小型部署，但不是容量声明的参考拓扑；进程内会话、队列和限流无法满足 FR-019。

## 2. PostgreSQL、连接池与迁移

- **Decision**: 生产使用 PostgreSQL、SQLAlchemy 2 异步会话、psycopg 3 与 Alembic；保留适用的 SQLModel 声明。每个工作单元使用独立 `AsyncSession` 和显式事务。连接池有上限、等待超时、健康检查和回收策略；生产可在 PgBouncer 事务池模式下使用小应用池或 `NullPool`。
- **Rationale**: 明确事务边界和连接预算才能多实例扩展；Alembic 的 expand/backfill/contract 迁移可在持续服务时逐步演进。
- **Alternatives considered**: `metadata.create_all()` 仅用于隔离测试；SQLite 保留为开发便捷路径，不能支撑生产一致性或容量结论。
- **Constraint**: 满足 `副本数 × 每副本最大连接 + 后台任务连接 + 管理预留 < PostgreSQL max_connections`。租户查询必须显式带 `tenant_id`，并用 PostgreSQL RLS 作纵深防御；应用角色不得拥有表或拥有 `BYPASSRLS`。

## 3. 单一 LangGraph 编排

- **Decision**: Chat、SSE Chat、Eval 和桌面文件工作流调用同一个类型化 LangGraph 核心图；使用 `langgraph-checkpoint-postgres` 持久化检查点。应用表将不可猜测的 `thread_id` 映射到 tenant/user/run，并在每次 invoke、stream 和 resume 前授权。
- **Rationale**: 一条图路径消除入口间证据门控与工具行为漂移；持久检查点支持中断、人工审批与重启恢复。
- **Alternatives considered**: 延续现有自定义 Pipeline 作为第二套长期编排会违反宪法 III；它只可作为限期迁移适配层。
- **Constraint**: Graph State 只保存 JSON 可序列化执行状态，不代替业务审计。审批使用 `interrupt()` / `Command(resume=...)`；中断前节点必须纯或幂等，外部副作用放在独立的批准后节点。

## 4. 后台任务与过载控制

- **Decision**: 使用 Celery 5 + RabbitMQ quorum queues 执行解析、索引、核对和沙箱调度；PostgreSQL `jobs` 与 transactional outbox 是权威状态，队列消息只是唤醒信号。Redis Lua token bucket 与有租期 semaphore 实施租户、用户和全局配额。
- **Rationale**: RabbitMQ 提供持久投递、发布确认和明确背压；数据库权威任务状态使重复投递、取消和恢复可核验。
- **Alternatives considered**: Redis broker 的运维较简单，但与缓存职责混合且持久投递边界较弱；Temporal 语义更强，但会与 LangGraph 的持久工作流职责重叠并扩大本次范围。
- **Constraint**: 固定工作负载队列而非每租户一队列；限制队列长度/字节，配置死信、手动确认、`prefetch=1`、有限重试与软硬超时。过载返回 `429` 或 `503` 和 `Retry-After`，不得无限排队。

## 5. Milvus 检索与版本切换

- **Decision**: 首期采用共享 Milvus collection，以 `tenant_id` 为 partition key；标量字段包含 knowledge base、document、不可变 document version、chunk、embedding model/version 和 `retrievable`。每次 ANN 查询在服务端表达式中同时过滤 tenant、knowledge base、可检索状态与当前 embedding version。
- **Rationale**: partition key 可降低大量租户的分区管理成本；不可变版本和发布状态可避免更新期间读到半成品或旧证据。
- **Alternatives considered**: 高监管租户可独立 collection 或 database，以更高运维成本换更强物理/RBAC 隔离；默认不为每租户建 collection。
- **Constraint**: 向量 ID 确定性生成。新版本先写入并核验为不可检索，再停用旧版本、切换 PostgreSQL active version，最后启用新版本；宁可短暂证据不可用，也不返回旧版本。初始索引采用 HNSW + COSINE，但最终参数须在真实语料规模下与 IVF 方案基准测试后确定。

## 6. 对象存储与跨存储一致性

- **Decision**: MinIO/S3 开启版本控制，使用不可变对象键 `tenant/document/version/random-id`；PostgreSQL 保存 bucket、key、VersionId、SHA-256、大小和媒体类型。跨 PostgreSQL、Milvus、对象存储使用 outbox + 可恢复 saga，资源状态至少覆盖 `pending_upload → scanning → indexing → available → deleting → deleted/error`。
- **Rationale**: 三个系统没有原生分布式事务；显式状态机、幂等步骤和周期核对能暴露并修复部分成功。
- **Alternatives considered**: 把文件字节放数据库或 Milvus 不符合存储职责；假设一次请求内三处写入原子成功不可恢复。
- **Constraint**: 物理删除先使向量不可检索，再删除所有对象版本和 delete marker，最后清理 SQL。周期核对缺失对象/分块、孤立向量/对象与版本漂移；Object Lock 只用于依法保留且明确不进入物理删除流程的数据。

## 7. 真实文件沙箱

- **Decision**: 每个文件任务启动一个使用 gVisor RuntimeClass 的短寿命 Kubernetes Job；非 root、只读根文件系统、移除 capabilities、seccomp、无 service-account token、无 `hostPath`、默认拒绝网络，并限制 CPU、内存、临时空间和截止时间。控制器只把已批准对象复制到 `emptyDir`，沙箱无云凭据。
- **Rationale**: 路径校验只能防一类逃逸，不能隔离恶意解析器、资源耗尽或主机访问；OS/容器级边界才符合“真实沙箱”。
- **Alternatives considered**: Kata Containers/Firecracker 隔离更强但启动与运维成本更高；首期先用 gVisor 并由攻击面测试决定是否升级。
- **Constraint**: 处理器镜像按格式最小化，使用固定 argv 调用，不提供 shell、包管理器或动态代码执行。按 magic bytes 验证格式，接入恶意内容扫描，并限制压缩层数、展开字节、文件数和压缩比；解析前解析真实路径并拒绝链接/快捷方式。

## 8. Electron 安全边界与 UI 验证

- **Decision**: Electron 主进程是最小 capability broker；renderer 启用 `contextIsolation` 和 sandbox、禁用 Node integration，使用严格 CSP、导航/新窗口拒绝和精确 sender origin 校验。`contextBridge` 只暴露按操作划分、schema 校验的 IPC；认证 API/SSE 由主进程代理，refresh token 使用 OS `safeStorage`。
- **Rationale**: renderer 内容不应直接获得文件、Node 或凭据能力；操作级 IPC 易于最小授权与审计。
- **Alternatives considered**: 把旧网页直接包装进 Electron 不满足 UI 验收；长期并存 Electron/Tauri 违反单一桌面架构边界。
- **Constraint**: renderer 销毁时取消待执行特权动作；生产包和更新签名。Electron E2E 采用 WebdriverIO Electron Service，并在真实应用常见窗口尺寸验证键盘、焦点、对比度和核心工作流。

## 9. SSE、取消与重放

- **Decision**: FastAPI 使用 `sse-starlette` `EventSourceResponse`、心跳、发送超时与有界 AnyIO channel。生产者在缓冲区满时阻塞或合并低价值进度事件；清理路径取消生产者、关闭订阅并释放并发租约。PostgreSQL 保存任务权威状态，Redis Streams 只保留有界、过期的重放事件。
- **Rationale**: 有界通道提供真实背压；业务恢复不依赖某一 HTTP 连接或无限事件日志。
- **Alternatives considered**: 进程内队列无法跨实例恢复；把每个 token 持久化到 PostgreSQL 成本高且不必要。
- **Constraint**: 支持 `Last-Event-ID`；命中缓存则重放，过期则发送当前状态快照。代理禁用缓冲并对齐 idle timeout；断连、`CancelledError` 和优雅停机均必须可观测地释放资源。

## 10. Claude/LLM 调用边界

- **Decision**: 通过官方 Anthropic Python SDK 的异步客户端调用 Claude；长输出采用 streaming，复杂请求使用 adaptive thinking。模型配置、最大输出、超时和供应商路由由受控配置管理，不在业务节点散落。并发在进入供应商请求前按 tenant/user/global 三层取得租约，令牌和费用使用量持久化。
- **Rationale**: SDK 已处理连接错误、408/409/429/5xx 的有限指数退避；入口配额和有界队列可避免供应商限流演变为内部雪崩。
- **Alternatives considered**: OpenAI-compatible shim 会丢失 Claude 原生语义；无限自定义重试会放大费用和副作用；Managed Agents 的托管沙箱与本项目自建 LangGraph、企业存储和本地沙箱边界重叠，故不作为核心编排。
- **Constraint**: 解析结构化 stop reason 和 SDK 类型化错误；仅对可重试失败重试并尊重 `Retry-After`，总次数和总时长受 Graph 节点预算约束。测试替身返回固定、可版本化的内容/工具调用/延迟/错误脚本，不调用外部供应商。系统容量与真实供应商延迟、限流、token 和成本结果分开运行、存储和报告。

## 11. 负载测试与可观测性

- **Decision**: Locust 分布式执行；非 LLM API 使用 `FastHttpUser`，SSE 使用独立用户实现并记录连接、首阶段、心跳、断开和清理。容量套件分别及组合运行 1,000 活跃会话与 1,000 SSE 连接，覆盖 smoke/load/stress/spike/30-minute soak、文件流程、队列饱和、实例终止和双租户隔离。采用 OpenTelemetry SDK → Collector → Prometheus/Grafana、Tempo、Loki。
- **Rationale**: 负载生成器与被测系统分离可识别生成器瓶颈；统一 traces/metrics/logs 可从 `run_id` 追到 API、图节点、队列、检索、存储与沙箱。
- **Alternatives considered**: 单机脚本或只测平均延迟无法支撑宪法容量声明；只测真实 LLM 会把供应商波动混入系统容量。
- **Constraint**: 保存 raw results、commit、拓扑、硬件、数据量和工作负载。监控生成器 CPU、文件描述符和临时端口。tenant/user 不作为 Prometheus 高基数 label，租户用量写 PostgreSQL。集成与故障测试使用 pytest、Testcontainers 和 Toxiproxy。

## 12. 增量迁移策略

- **Decision**: 严格采用九个可独立运行阶段：容量基线；无状态 API + PostgreSQL；共享 LangGraph；持久任务/配额；Milvus + 对象存储；沙箱 + RBAC；安全 Electron 外壳；完整 UI 重设计；综合验收与旧 UI 退出。
- **Rationale**: 每阶段都有可观察交付和退出门，可避免全栈重写后才发现兼容、安全或容量问题。
- **Alternatives considered**: 一次性替换风险无法隔离；永久双写或双编排会产生状态与结论漂移。
- **Constraint**: 临时适配器必须有删除条件、owner、截止阶段和等价性测试；跨阶段写路径不得永久双主。旧 UI 仅在新桌面能力对等和数据迁移 100% 验收后移除。

## Resolved Clarifications

Phase 0 已解决所有技术上下文未知项。尚未由测试决定的容量参数（Milvus 索引参数、连接池大小、Pod/worker 数、队列容量、沙箱并发和 LLM 配额）不是规格缺失，而是必须由 Phase 1 基线与后续负载证据标定的配置值；计划中不得预先宣称达标。

