# US5 管理员治理 — 证据（T148）

目标：用**可重跑**的方式证明两件事——(1) 管理员只能治理本租户，跨租户能力需显式授予；(2) 预置的跨存储问题、失败任务与配额/用量都能被发现、处理并留下脱敏审计。

## 如何复现

```bash
# 证据（生成 rbac-matrix / seeded-faults / summary）
python scripts/collect_us5_evidence.py

# 后端测试
python -m pytest tests/security/test_admin_rbac.py tests/integration/test_audit_trace.py \
  tests/integration/test_admin_capacity.py tests/integration/test_admin_reconciliation.py -q

# 前端测试（在 frontend/ 下）
npx vitest run src/api/admin.test.ts src/features/admin/reconciliation-page.test.tsx
```

## 文件

| 文件 | 内容 |
| --- | --- |
| `rbac-matrix.md` / `rbac-matrix.json` | 4 主体 × 9 端点的**实际**状态码与稳定错误码 |
| `seeded-faults.json` | 预置 finding 列表、health 聚合、脱敏时间线、恢复前后状态、grant 生命周期、retry/resolve 响应 |
| `summary.json` | 环境信息 + 18 项布尔检查 + 结论（`passed`） |
| `pytest-us5.txt` | 后端四套件原始输出（32 passed） |
| `vitest-us5.txt` | 前端两文件原始输出（5 passed） |

## 本轮结果

- 后端 **32 passed**（SQLite，完整组装应用）。
- 前端 **5 passed**；`tsc -b` 与 ESLint 干净。
- 另跑回归：契约/集成 **40 passed**；真实 PostgreSQL 17.10 四套件 **30 passed**。
- `summary.json` 的 18 项检查全部为 `true`。

矩阵中的三条关键边界：

1. `alpha_employee` 对**所有**读取都是 `403 AUTH_FORBIDDEN`，且拒绝路径会先写审计再返回。
2. `beta_admin` 读 alpha 的 run、`alpha_admin` 读 beta 的 run 都是 `404 RESOURCE_NOT_FOUND`——与「不存在」同形，无法用来枚举其他租户。
3. `/capacity` 对租户管理员 `403`，只有持有独立 `cross_tenant_admin` 的平台操作员 `200`。

## 诚实边界

- 证据在 SQLite 上生成；PostgreSQL 的对应覆盖由 pytest 套件提供（`artifacts/...` 未重复保存 PG 输出）。
- worker 的调度只实现 `run_forever(interval, stop_event)`，**未**验证生产进程管理器 / Celery beat 的接线；`repair=True` 只验证了参数透传与转换审计，未对真实 Milvus/MinIO 做破坏性修复演练（那属于 Stage 5 reconciliation 的测试面）。
- 前端三页经过类型检查、lint 与两处组件测试，**未**在真实 Electron/浏览器里人工走查；Phase 7/8 的 E2E 不覆盖新增路由。
- `capacity` 的 403 解释由后端错误驱动，前端不猜角色；若后端将来改为返回空列表，本页会显示「无记录」而不再解释权限——这是刻意不伪造的取舍。

## 已知遗留（非本阶段范围）

- T137 的目标员工可用性研究仍为 `[~]`（无真实被试）。
- `T092`/`T098` 的 gVisor 运行时隔离仍为 `[~]`（本机无 K8s 集群）。
