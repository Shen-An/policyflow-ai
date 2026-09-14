# 12. PostgreSQL 与多租户设计（Stage 2 数据面权威）

版本：v1.0
日期：2026-09-14
状态：**部分落地**（见 §7 差距清单）
适用范围：生产数据面。本文是 PostgreSQL、多租户隔离与迁移策略的**权威设计**，取代 `02-database-design-sqlite.md` 与 `01-architecture-design.md` 中关于「SQLite 即数据权威」的表述。

---

## 1. 文档地位

| 文档 | 记录什么 | 是否仍然权威 |
|------|----------|--------------|
| `01-architecture-design.md` | v0.1 MVP 应用架构（RAG、Skill、Tool、MCP） | 应用层权威；**存储层表述已被本文取代** |
| `02-database-design-sqlite.md` | 开发库 SQLite 的表结构与字段语义 | 仅描述 **dev 库**；不再是生产权威 |
| `12-postgresql-multitenancy-design.md`（本文） | 生产数据权威、租户模型、迁移策略、隔离机制 | **是** |

两者并存不是设计目标，而是迁移过程中的事实。Stage 9（见 `specs/001-enterprise-agent-refactor/tasks.md`）之前，SQLite 仍作为开发便利存在。

---

## 2. 不变量

以下不变量是设计契约。违反它们属于缺陷，而不是实现细节分歧。

1. **PostgreSQL 是唯一的生产 SQL 权威。** SQLite 只服务开发与隔离测试，生产不允许 `metadata.create_all()`，启动只校验 schema 版本。
2. **正确性不得依赖进程内状态。** 进程锁、内存队列、进程内缓存、启动时迁移都不得成为业务正确性的前提——两台实例共享同一份数据必须看到同一结果。
3. **每个受保护的数据访问都必须显式传 `tenant_id`。** 由调用方显式给出，不允许从请求体、查询参数或隐式的「当前租户」推断。
4. **跨租户与不存在必须不可区分。** 两者返回同一错误类型、同一 code、同一 HTTP 状态；错误消息不得泄漏属主租户。
5. **RLS 是纵深防御，不是主要控制。** 主要控制是应用层授权；RLS 用于兜住「有人漏写谓词」的情况。
6. **证据优先于声明。** 隔离性、迁移正确性必须由可重跑的命令与保留的原始输出证明，不能以摘要代替。
7. **迁移分阶段且可中断续跑。** 加列（expand）与收紧约束（enforce）必须分离，中间的回填可被打断并从中断处继续。

---

## 3. 租户模型

### 3.1 归属根

`tenants` 是归属根：它有 `code`（全局唯一）与 `status`，**自身不带 `tenant_id`**。任何把 `tenant_id` 加到 `tenants` 上的做法都会造成自引用，属于缺陷。

一个租户只对自己可见：`tenant_id == 自身` 时可见，否则一律不存在。

### 3.2 现有覆盖范围

当前元数据共 **32 张表**，其中 **19 张已带 `tenant_id`**：

```
agent_runs, ai_query_logs, audit_events, conversations, drafts, eval_cases,
eval_results, eval_runs, graph_checkpoint_bindings, idempotency_records,
knowledge_bases, knowledge_documents, memory_items, messages,
retrieval_eval_items, roles, run_events, user_role_grants, users
```

**13 张尚未带租户归属**，其中 `tenants` 是正确的，其余 12 张是**真实缺口**：

```
audit_logs, departments, faq_drafts, knowledge_base_permissions, mcp_servers,
model_providers, query_feedback, rag_index_jobs, skills, tool_call_logs,
tools, user_roles
```

这些表里 `faq_drafts`、`knowledge_base_permissions`、`query_feedback`、`rag_index_jobs`、`tool_call_logs` 都持有租户相关数据，**在补齐归属之前不能声称「全链路租户隔离」**。`user_roles` 是 `user_role_grants` 之前的旧成员表，属于待退役对象。

### 3.3 业务标识的唯一性

租户化之前业务标识是全局唯一的，这会让第二个租户无法复用同名标识。已转为**按租户唯一**（复合唯一约束 `uq_<table>_tenant_<column>`）：

| 表 | 列 |
|----|----|
| `knowledge_bases` | `code` |
| `users` | `username`、`email` |
| `run_events` | `event_id` |
| `audit_events` | `event_id` |

仍然全局唯一的业务标识还有 `departments.code`、`skills.name`、`tools.name`、`mcp_servers.name`、`model_providers.name`。它们**没有 `tenant_id`**，因此无法表达复合唯一；在补齐归属前保持全局唯一是刻意选择，不是遗漏。

### 3.4 乐观并发

可变聚合带 `version` 列，更新走 compare-and-set：`WHERE id AND (tenant) AND version = expected`，命中则 `version + 1`。零行受影响是**歧义**的，因此先探测存在性：

- 探测不到 → `ResourceNotFoundError`（不存在或属于别的租户，不可区分）
- 探测到但版本不符 → `VersionConflictError`

`users`、`roles`、`user_role_grants`、`agent_runs`、`graph_checkpoint_bindings`、`idempotency_records` 均参与 compare-and-set。

---

## 4. 迁移策略

### 4.1 四阶段

| 阶段 | 目的 | 当前状态 |
|------|------|----------|
| `expand`（`001`） | 加列、建表、创建 `legacy` 租户、建 RLS policy；`tenant_id` 保持可空 | 已实现 |
| `backfill`（`migrations/backfill_legacy_tenant.py`） | 给历史行补归属；可中断续跑 | 已实现 |
| `enforce`（`002`） | `tenant_id` 转 NOT NULL、按租户唯一、强制 RLS | 已实现 |
| `contract` | 退役旧列与旧表（如 `user_roles`、`audit_logs`） | **未实现** |

### 4.2 应用顺序

```powershell
alembic upgrade 001
python -m migrations.backfill_legacy_tenant
alembic upgrade head
```

**直接执行 `alembic upgrade head` 会失败，这是设计如此**：enforce 阶段先核对回填账本，未回填即拒绝，避免把无归属的数据锁进 NOT NULL。

### 4.3 回填的可恢复性

- 账本表 `migration_backfill_state` 持久化每张表的状态：`pending` / `running` / `completed` / `failed`。
- 记录 source/target 计数、逐表 checksum，以及聚合 checksum（便于跨环境比对）。
- 批处理带 `--batch-size`；`--stop-after-batches` 用于人工制造中断以验证续跑；`--report-only` 只出核对报告。
- 父行缺失等无法推断归属的行会写入 `failures` 并给出明确原因码（如 `parent_row_missing`），而不是静默归到 `legacy`。
- 已实测：中断后续跑可以收敛，且 `verify_backfill` 在任何 `tenant_id` 仍为 NULL 时报错。

### 4.4 legacy 租户

```
LEGACY_TENANT_ID   = 00000000-0000-0000-0000-000000000001
LEGACY_TENANT_CODE = legacy
```

历史行全部归它所有。种子数据（角色、知识库、引导管理员）**加入**同一个租户，而不是另建一个根——否则同一个库里会出现两套互不可见的参照数据。

---

## 5. 隔离机制

### 5.1 应用层（主要控制）

- Repository 与 Unit of Work 的每个受保护方法都要求显式 `tenant_id`；缺失或为空时抛 `TenantScopeError`，**不**退化为「全部租户」。
- 租户谓词是 SQL 的一部分，不是取回后再过滤——后者会在内存里出现越权数据。
- 跨租户读取、更新、删除与「不存在」返回同一个 `ResourceNotFoundError`。
- 枚举（列表）同样受租户谓词约束：枚举是最容易泄漏的地方。
- 唯一约束冲突按数据库 sqlstate `23505` 判定；其他 `IntegrityError`（如 NOT NULL 违反）**不得**被翻译成业务冲突，否则真实缺陷会被伪装成正常业务分支。

### 5.2 数据库层（纵深防御）

每张租户表都建立同名策略：

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
  USING      (tenant_id::text = NULLIF(current_setting('policyflow.tenant_id', true), ''))
  WITH CHECK (tenant_id::text = NULLIF(current_setting('policyflow.tenant_id', true), ''));
```

关键点，每一条都有踩坑记录：

- **`ENABLE` 与 `FORCE` 缺一不可。** `FORCE` 只改变策略约束谁（含表属主），**不会开启**行安全。只发 `FORCE` 会得到「有策略但从不生效」的假保护。此问题曾真实存在于 5 张表上。
- **未设租户 = 零行。** `NULLIF(..., '')` 让策略在会话未声明租户时拒绝一切，而不是放行一切。
- **GUC 用 `set_config('policyflow.tenant_id', <值>, false)` 设置。** `SET x = $1` 不接受绑定参数（语法错误）；autocommit 下 `SET LOCAL` 是空操作。
- **应用角色必须受限**：`policyflow_app` 需 `rolsuper = false` 且 `rolbypassrls = false`，且不拥有这些表。
- **超级用户绕过 RLS。** 因此验证隔离必须 `SET ROLE policyflow_app` 后用**非超级用户**身份断言；用超级用户跑 RLS 用例等于没测。

### 5.3 枚举安全

租户不可见必须与不存在返回同一形状（同一异常类型、同一 code、同一状态码）。消息里回显调用方自己传入的 id 是可接受的（未泄漏新信息），但**不得**出现属主租户标识。

---

## 6. 连接与会话

- async SQLAlchemy 2 + `psycopg` 3；迁移脚本走 Alembic 的 async 引擎。
- 连接池有上限与等待超时，可回收；健康检查探针只读、不改 schema。
- **Windows 必须用 `WindowsSelectorEventLoopPolicy`**：async psycopg 拒绝 `ProactorEventLoop`（默认策略），否则连不上库。该策略在 `backend/app/db/session.py` 导入时设置。
- PgBouncer 作为事务级连接池位于应用与 PostgreSQL 之间；dev 编排在 `infra/dev/compose.yaml`（端口整体偏移，避免与本机既有 PostgreSQL/Redis 冲突）。

---

## 7. 差距清单（未实现，勿假设已具备）

1. **`/health` 不校验 schema 版本。** 进程存活即返回 200，迁移到一半的库同样返回 200。就绪探针需要区分「库不可达」「库未迁移」「revision 与 head 不符」——相关函数 `check_database_ready()` 已存在并有测试，但**尚未接到 HTTP 路由**（T034）。
2. **API 层尚未接入 principal 与 async Unit of Work。** 现行路由仍用同步 Session 依赖；租户身份由 `user.tenant_id` 间接获得，尚未强制「禁止从 body/query 接受 tenant/user」（T033）。
3. **service 层未整体租户化。** 只有 `knowledge_base_service.py` 做了最小改动；`memory_service.py`、`eval_service.py` 仍是单租户语义（T035）。
4. **`contract` 阶段不存在。** 旧表 `user_roles`、`audit_logs` 与旧列仍在。
5. **12 张表仍无租户归属**（见 §3.2）。
6. **RLS 只在 PostgreSQL 上存在。** 用 dev SQLite 验证租户隔离是无效的：SQLite 没有行级安全，`enforce` 迁移也不在 dev 路径执行。
7. **dev 库与生产 schema 不完全等价。** 开发库的原地补列无法为「NOT NULL 且无默认值」的列生成 DDL，此类列只会被记录并跳过。

---

## 8. 如何验证

```powershell
# 应用与测试（需要本地 PostgreSQL 时设置）
$env:POLICYFLOW_TEST_DATABASE_URL = "postgresql+psycopg://<user>:<pass>@127.0.0.1:55432/policyflow_test"

pytest tests -q --ignore=tests/load
```

需要 PostgreSQL 的套件：`tests/integration/test_postgres_migrations.py`（迁移可重启、enforce 拒绝未回填、RLS 选择性、按租户唯一）、`tests/integration/test_multi_instance.py`（两实例共享状态、重启存活、生产拒绝 SQLite）、`tests/security/test_tenant_isolation.py`（租户谓词、枚举不可区分、受限角色下的 RLS）。

**没有 PostgreSQL 时这些用例无法运行——跳过不等于通过。**

迁移链路的端到端原始输出保存在 `artifacts/migration/stage2/`：

| 文件 | 内容 |
|------|------|
| `migration_flow_raw.txt` | expand →（正确拒绝）→ backfill → enforce 七步证明 |
| `pytest_integration_raw.txt` | 上述套件的 `-rA` 原始日志 |

复现迁移证明：`python scripts/_verify_stage2_migration_flow.py`
