# Quickstart validation (T164)

按 `specs/001-enterprise-agent-refactor/quickstart.md` 的适用命令逐条记录。**记录实际结果，不补跑不存在的步骤。**

| 命令 | 结果 | 说明 |
|---|---|---|
| `python start.py --no-browser --port 8123` + `GET /health` | **200 `{"status":"ok"}`** | 真实进程启动并响应；日志 `quickstart-server.log`、响应 `quickstart-health.txt`。启动后已停止进程。 |
| `pytest -q tests/contract tests/eval tests/integration tests/migration tests/reconciliation tests/recovery tests/security` | **626 passed, 196 skipped, 1 failed** in 387s | 1 failed = `tests/integration/test_multi_instance.py::test_readiness_distinguishes_unmigrated_from_unreachable`，原因是本机 PostgreSQL 未启动导致 `psycopg.ConnectionTimeout`；该用例没有把它当作 skip。196 skipped 主要来自 PG / Milvus / MinIO / RabbitMQ 依赖。输出与 SHA-256 见同目录 `pytest.txt` / `sha256.txt`。 |
| `npm --prefix frontend run test` | **47 files / 155 passed** in 33.9s | 输出见 `desktop/vitest.txt`，退出码 0。 |
| `npm --prefix frontend run test:electron:e2e` | **8 failed / 0 passed**（30s） | 真实执行过：`electron.exe --version` 返回 v24.20.0，但 WDIO 启动时 `Chrome failed to start: crashed`（`DevToolsActivePort file doesn't exist`）。本会话没有可用的桌面会话，Electron 进程无法创建窗口。证据：`desktop/electron-e2e.txt`、`-exit.txt`、`-sha256.txt`。 |
| `python scripts/acceptance_legacy_read_compare.py` | **ok, 3 rows** | SQLite 种子数据上，legacy `drafts` 读形态与 `project_change_set` 投影在 `draft_type`/`status`/`title`/`related_sources` 四个字段逐条一致；输出 `legacy-read-parity.json`。**不等于**生产副本已比对。 |
| `python scripts/t152_legacy_column_consumers.py` | **gate_cleared=false（45 处）** | 只读 preflight，未删任何列；列出仍引用 `Draft`/`drafts` 的模块（`routes_draft.py`、`db/base.py`、`schemas/draft.py` 等）。输出 `t152-consumers.json`。 |
| `python scripts/acceptance_legacy_audit.py` | **eligible_for_removal=false** | 两个面现在由 durable ledger 计数（`POLICYFLOW_LEGACY_LEDGER`，默认 `artifacts/acceptance/legacy-usage.json`）；ledger 不存在 = 未测量，仍然阻断。 |
| `npm --prefix frontend run typecheck` / `build` | **未在本轮重跑** | 上一轮（R38 前）绿；本轮前端只改了新增文件之外的 README，未触及 TS。 |
| `alembic upgrade 001` → `python -m migrations.backfill_legacy_tenant` → `alembic upgrade head` | **未执行** | 需要真实 PostgreSQL；本轮 PG 实例未启动。迁移链本身由 `tests/migration/test_alembic_scaffold.py` 覆盖（需 PG，已 skip）。 |
| `docker compose -f infra/dev/compose.yaml up -d` | **未执行** | 本轮不启动企业栈；`artifacts/acceptance/` 中的存储相关证据因此不完整。 |

## 2026-10-21 复跑（上面的表是 2026-10-18 的快照，数字已过期）

| 命令 | 结果 | 说明 |
|---|---|---|
| `pytest -q tests` （全量） | **942 passed, 197 skipped, 0 failed** in 300.06s | 输出 `pytest-full-2026-10-21b.txt`。上一轮记的那 1 failed 已修：`test_multi_instance.py` 的 `require_pg_server()` helper（34b0633）在 PG 不可达时 skip 而不是 fail，本机 PG 未起时实测 1 skipped。另修了两处：`AgentPipeline.legacy_counters` 类级默认值（`test_pipeline_wraps_the_entire_turn_timeout` 用 `object.__new__` 绕过 `__init__` 导致 AttributeError）；`JobService.lease(lanes=...)` 补上 lane 过滤，`tests/contract` 由 22 passed + 1 xfail 变为 23 passed + 0 xfail。 |
| `pytest -q tests/contract tests/eval tests/integration tests/migration tests/reconciliation tests/recovery tests/security` | **664 passed, 197 skipped, 1 xfailed** in 259.64s | 子集复跑，输出 `pytest-rerun-2026-10-21.txt`（该次跑在 lane 过滤修复之前，故仍带 1 xfailed）。 |
| `npm --prefix frontend run lint` / `typecheck` / `test` | **0 problem / 0 error / 48 files 162 passed**（36.42s） | Phase 1–6 的 12 error + 1 warning 已清零（拆分 `button-variants.ts`、`chip-tone.ts`、`use-color-mode.ts`，详情见 `docs`/提交说明），旧页面不再带 lint 债。 |
| `npm --prefix frontend run test:electron:e2e` | **8 failed / 0 passed**（29s） | 仍未跑通，但根因已定位到环境：直接 `electron.exe --app=dist-electron/main/index.cjs` 立即退出、无输出，exit `-2147483645`(0x80000003 STATUS_BREAKPOINT)；加 `--no-sandbox --disable-gpu` 后报 `Lock file can not be created: 拒绝访问 (0x5)` + `GPU process launch failed` 并退出。详见 README「没做/没验过」第 1 条。 |

## 发布判断

**不满足发布条件。** 依据：

1. ~~后端全量套件有 1 个失败~~ → 2026-10-21 复跑已 **0 failed**（942 passed / 197 skipped），PG 不可达的用例现在按 skip 处理。发布阻塞点不再是失败用例本身。
2. Electron E2E、迁移链、企业栈三条 quickstart 路径未在目标环境执行；Electron 的阻塞已确认在宿主环境（Chromium 起不来），换一台有桌面会话的机器需要复跑一次。
3. SC-001–SC-006 没有容量数字；SC-010/011 没有真实被试；SC-016 没有数据权威切换。

可发布的部分是**开发/演示路径**（浏览器 SPA 启动 + 健康检查 + 两套测试），这与 README 的定位一致。
