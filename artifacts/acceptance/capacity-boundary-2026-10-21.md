# 容量 / 压测边界（2026-10-21）

结论先说：**本轮仍然没有产能数字**（SC-001 ~ SC-005 全部 inconclusive，SC-006 仍 partial）。
本轮新增的是「能证明的基础设施证据」+「把说谎的空间关掉」，不是吞吐结论。
任何对外材料在没跑完验收环境之前，都不应引用本节以下的并发/时延/失败率之外的推断。

## 1. 本轮真实跑过的东西

| 项 | 命令 | 结果 | 日志 |
| --- | --- | --- | --- |
| 容量机制（真 Redis，进程内 ASGI，不打 LLM） | `pytest tests/load/test_stage4_capacity.py -q` | **3 passed in 7.73s** | `artifacts/acceptance/load/pytest-stage4-capacity-2026-10-21.txt` |
| 压测 profile / 判定逻辑 | `pytest tests/load -q` | 29 passed | 见 `artifacts/acceptance/quickstart-validation.md` |

`test_stage4_capacity.py` 三条用例验的是**过载行为**，不是产能：

1. 100 并发 `POST /api/v2/runs` 打满预算后，多余的请求得到 `429` / `503` + `Retry-After`，**零 5xx**（不崩、不排队到超时）。
2. 并发 SSE replay（N=10）结束后 active-SSE gauge 归 0，teardown 计入 cleanup histogram（连接不泄漏）。
3. Redis 短中断时 admission **fail-closed**：`503` + `ADMISSION_UNAVAILABLE` + `Retry-After`，Redis 恢复后重新 `201`。

这三条是「限流与降级不会骗人」的证据，可以写进面试叙事；**不能**推导出「支持 1000 会话」。

## 2. 本轮环境（决定哪些跑不了）

| 依赖 | 状态 | 影响 |
| --- | --- | --- |
| Redis `127.0.0.1:6379` | **可用** | 所以容量机制类用例能真跑 |
| PostgreSQL `127.0.0.1:55432` | 未启动 | PG 相关用例 skip（含多实例 readiness、T149 的 PG 对照） |
| RabbitMQ `5672` | 未启动 | 分布式 broker 消费者（T064/T072）不可验 |
| 独立压测机 / Locust 分布式 | 无 | SC-001 ~ SC-005 的 1000 并发、30 分钟 soak 只能标 inconclusive |

环境快照：`Windows-10-10.0.26200-SP0`，Python 3.11.15（conda `policyflow`），宿主不允许 Chromium 起 GPU/单实例锁（详见 README 的 Electron 段，与本文件无关但同属「宿主机限制」类）。

## 3. 各条验收的诚实判定（与 `SC-001-SC-016.md` 一致）

| 项 | 判定 | 为什么 |
| --- | --- | --- |
| SC-001 1000 会话 / 失败率 < 1% | inconclusive | profile 已能按 `WORKFLOW_WEIGHTS` 真分流并统计 `active_sessions`，但没在验收环境跑 |
| SC-002 1000 SSE / 30s 清理 | inconclusive | 清理语义有 `tests/integration/test_sse_*`，1,000 并发与 30s 上限没测 |
| SC-003 200 RPS / 非 LLM p95 < 500ms | inconclusive | `evaluate_acceptance` 会从 `raw.csv` 现算（`NON_LLM_ROUTES` 显式分类），没有 `raw.csv` 就没有结论 |
| SC-004 首阶段 p95 < 1s | inconclusive | 首阶段改为**逐帧**计时（读到第一个 SSE `event:`/`data:`），需要真实 provider 流量 |
| SC-005 30 分钟 soak | inconclusive | 缺指标或缺实际会话/SSE 并发数时，判定函数自己就返回 inconclusive |
| SC-006 容量声明附可复现报告 | partial | 报告结构/哈希/环境字段/`summary.json` 全就绪，**只缺数字** |

本轮**没有**放宽任何判定：`tests/load/test_acceptance_profile.py` 里「缺指标 → inconclusive」「并发数不足 → fail」的规则一条没动。

## 4. 要拿到数字，需要什么

一次性、有明确前置条件清单，避免下次又「以为跑了」：

1. 一台独立压测机（与 API 不同机，避免 CPU 争抢把 p95 说成宿主噪声）。
2. API 侧：PG + Redis + RabbitMQ 全起（`docker compose up -d` 或验收环境部署），`POLICYFLOW_LOAD_PROFILE=acceptance`。
3. 真 provider key（或明确接受 LLM 阶段用 mock 并在报告里标注哪几段是 mock）。
4. 跑：`POLICYFLOW_LOAD_PROFILE=acceptance locust -f tests/load/locustfile.py`，落 `raw.csv` + `summary.json`，再 `evaluate_acceptance` 出 verdict。
5. 三条硬门槛（脚本会自己判）：`active_sessions` 峰值 ≥ 1000、`sse_streams` ≥ 1000、soak 连续 30 分钟无 error rate 超限。

## 5. 复现命令

```powershell
# 1) 容量机制（本机可跑，只需 Redis）
E:\Coding\Anaconda\envs\policyflow\python.exe -m pytest tests/load/test_stage4_capacity.py -q

# 2) 判定逻辑与 profile 自检
E:\Coding\Anaconda\envs\policyflow\python.exe -m pytest tests/load -q

# 3) 验收压测（需专用环境，本机不要跑）
$env:POLICYFLOW_LOAD_PROFILE="acceptance"
locust -f tests/load/locustfile.py
```

## 6. 对外表述口径

- 可以说：*「限流与降级路径有真 Redis 下的过载测试（100 并发零 5xx、SSE 不泄漏、Redis 抖动 fail-closed）」*。
- 可以说：*「压测脚本与判定规则已就绪，`requires_dedicated_environment`：没有专用环境，所以产能数字留空而不是编造」*。
- **不要说**：*「支持 1000 并发 / 200 RPS / p95 < 500ms」* —— 这三句今天没有数据支撑。
