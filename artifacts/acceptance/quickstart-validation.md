# Quickstart validation (T164)

按 `specs/001-enterprise-agent-refactor/quickstart.md` 的适用命令逐条记录。**记录实际结果，不补跑不存在的步骤。**

| 命令 | 结果 | 说明 |
|---|---|---|
| `python start.py --no-browser --port 8123` + `GET /health` | **200 `{"status":"ok"}`** | 真实进程启动并响应；日志 `quickstart-server.log`、响应 `quickstart-health.txt`。启动后已停止进程。 |
| `pytest -q tests/contract tests/eval tests/integration tests/migration tests/reconciliation tests/recovery tests/security` | **626 passed, 196 skipped, 1 failed** in 387s | 1 failed = `tests/integration/test_multi_instance.py::test_readiness_distinguishes_unmigrated_from_unreachable`，原因是本机 PostgreSQL 未启动导致 `psycopg.ConnectionTimeout`；该用例没有把它当作 skip。196 skipped 主要来自 PG / Milvus / MinIO / RabbitMQ 依赖。输出与 SHA-256 见同目录 `pytest.txt` / `sha256.txt`。 |
| `npm --prefix frontend run test` | **47 files / 155 passed** in 33.9s | 输出见 `desktop/vitest.txt`，退出码 0。 |
| `npm --prefix frontend run test:electron:e2e` | **未执行** | 本轮无 Electron/显示环境；不以此前阶段的 8/8 结果冒充本轮验收。 |
| `npm --prefix frontend run typecheck` / `build` | **未在本轮重跑** | 上一轮（R38 前）绿；本轮前端只改了新增文件之外的 README，未触及 TS。 |
| `alembic upgrade 001` → `python -m migrations.backfill_legacy_tenant` → `alembic upgrade head` | **未执行** | 需要真实 PostgreSQL；本轮 PG 实例未启动。迁移链本身由 `tests/migration/test_alembic_scaffold.py` 覆盖（需 PG，已 skip）。 |
| `docker compose -f infra/dev/compose.yaml up -d` | **未执行** | 本轮不启动企业栈；`artifacts/acceptance/` 中的存储相关证据因此不完整。 |

## 发布判断

**不满足发布条件。** 依据：

1. 后端全量套件有 1 个失败（环境导致的 PG 连接超时），即使可归因于环境，也不能在报告里写成"全绿"。
2. Electron E2E、迁移链、企业栈三条 quickstart 路径未在目标环境执行。
3. SC-001–SC-006 没有容量数字；SC-010/011 没有真实被试；SC-016 没有数据权威切换。

可发布的部分是**开发/演示路径**（浏览器 SPA 启动 + 健康检查 + 两套测试），这与 README 的定位一致。
