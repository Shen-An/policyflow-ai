# 复核意见处理记录（2026-10-21）

对应输入：`progress-review-2026-10-21.md`。本文只记录**这一轮真实做了什么、证据在哪、哪些没做及为什么**；没有做的事不写成“已解决”。

代码基线：`34b0633`（`master`）。本轮的改动尚未提交，工作区即证据。

## 结论速览

| 复核项 | 状态 | 证据 |
|---|---|---|
| 2. Electron 与真实后端启动协议不一致 | ✅ **已修** | `frontend/electron/main/api-proxy.test.ts`（7 passed） |
| 3. 验收压测分流 / SSE 计时 / 并发判定 | ✅ **代码已修**（运行仍未做） | `tests/load`（29 passed） |
| 补充：pytest 组合运行的停滞/失败根因 | ✅ **已定位并修** | 见 §3，`tests/load` 43s→8s |
| 4. T035 剩余 tenant 感知 | 🟡 **部分收敛**（见 §4，不含全量 async 迁移） | `tests -k "eval or tenant or isolation"`（101 passed / 34 skipped） |
| 1. T053 单图收敛 | ⛔ **未做**（见 §1，附下一步） | — |
| 文档与证据维护（6 条） | ✅ **已按复核更正** | §5 |

## §1 单一编排（T053 / FR-001）：未做，且不假装做了

复核判断成立：`graph/route_adapter.py` 仍然只是「记录遥测 + 委派 `send_chat_message` / `iter_chat_events` / `execute_eval_run`」，实际控制流在 `pipeline_graph.py`（由 `AgentPipeline` 驱动的进程内图），而文件/授权流程走 `builder.py` 的持久图。所以现状是**两张图**：Chat/Eval 一张，文件流程一张。

把 Chat/Eval 迁到 `GraphService` 不是“补个适配器”，它要求先把前端的完整输出契约（router / plan / skill / compliance / diagnostics / ToT 双请求暂停）移植进持久图状态，并让 `chat_service.py`（1202 行）的持久化与 SSE 事件顺序在新编排下逐条对齐——这是一次跨 `agents/`、`graph/`、`services/`、`api/` 的结构性改造，回归面覆盖整个 Chat/Eval 契约套件。本轮没有做，理由是：在没有验收环境、且 T035 的 async/tenant 基础还在迁移中的时候做大编排搬迁，只会把一个“诚实的两图现状”换成“两图 + 半迁移”，比现在更难验收。

下一步（可直接执行的最小切片，按顺序）：

1. 在 `graph/state.py` 定义 `ChatTurnState`（含 `router_result` / `plan_steps` / `skill_hits` / `compliance` / `diagnostics`），把 `pipeline_graph` 的 `route/tot/execute` 节点体搬到 `graph/nodes.py` 的持久图节点，节点实现零改写复用。
2. 用 `GraphService` 跑一次影子模式（`ROUTE_VIA_GRAPH_ADAPTER` 已存在），对比两边 `ChatResponse` 字段与 SSE 事件序列，差异清零前不切流量。
3. 差异清零后把 `route_adapter` 的委派改成真调用，再谈“旧路径退出”观察窗口。

## §2 Electron ↔ 后端 run 启动协议：已修

原问题：`api-proxy.ts::startRun` 发 `{kind, input, knowledge_base_ids, idempotency_key}`，而后端 `RunRequest` 只收 `{kind, payload, priority_lane, max_attempts}` 且**要求 `Idempotency-Key` 头**，按后端代码会 400。

改动：

- `toRunRequestBody()` 生成 `{kind, payload, priority_lane, max_attempts}`，`knowledgeBaseIds` 折进 `payload.knowledge_base_ids`（不再出现在顶层）；
- `resolveIdempotencyKey()`：16..128 字符内用调用方的 key（保持重试幂等语义），否则生成 `run-<uuid>`；header 走 `requestJson` 新增的 `headers` 选项；
- `runStartSchema` / `RunStartRequest` 同步新字段（`priorityLane`、`maxAttempts`），`idempotencyKey` 收紧到 16..128；
- `toRunSummary` 现在读 `state`（后端 run view 用的字段），不再只认 `status`；
- `login` 支持可选 `tenantCode` → `tenant_code`（多租户登录需要租户选择器）。

证据：`npm --prefix frontend run test -- electron/main/api-proxy.test.ts electron/main/run-mapper.test.ts electron/main/schemas.test.ts` → **18 passed**，其中 `api-proxy.test.ts` 断言了“顶层不得出现 `input` / `knowledge_base_ids` / `idempotency_key`”和 header 长度窗口。

**边界**：这修的是协议层；Electron → 真实 API → 任务 → 事件 → 审批的闭环联调仍未做（需要可启动的桌面会话，见 §6 Electron E2E 失败记录）。

## §3 验收压测：代码前置条件已补齐

复核的三点全部落实：

1. **分流**：`request_workflow` 之前只认 `sse/file/isolation`，`acceptance` 落回 `chat`，`workflow_weights` 形同装饰。现在 `_workflow()` 在 `acceptance` 下用 `weighted_workflow(WORKFLOW_WEIGHTS, rng)` 真抽样，四类工作流都会被执行（单测断言 500 次抽样覆盖 `{chat,sse,file,isolation}`）。
2. **逐帧 SSE 计时**：`_sse` 改成 `stream=True` + `iter_lines()`，读到第一个 `event:`/`data:` 的耗时记为 `sse_first_event_ms`；原来的 `response.text` 只能给出“整段响应结束”的耗时，不能证明首阶段 p95。
3. **实际并发判定**：新增 `ConcurrencyTracker`（`CONCURRENCY`），围绕 chat 与 SSE 请求统计在飞数量与峰值；`evaluate_acceptance` 把 `active_sessions` / `sse_streams` 变成真门禁——
   - 缺这两个指标 → `inconclusive`（此前只给吞吐/延迟/失败率/清理/soak/RSS 会 pass）；
   - 低于 1,000 → `fail`。
4. 顺带补齐：用户按 `next_user_slot()` **分散到不同租户**、登录带 `tenant_code`；隔离探针从“读自己的知识库列表”改成**用另一个租户的 token 读本租户资源并断言被拒**，违规计入 `isolation_violations`；跑完在 artifact 目录落 `summary.json`（并发峰值 / 非 LLM RPS / 首阶段 p95 / 失败率 / RSS 比值 / verdict），全部由 `raw.csv` + `resource.jsonl` 现算，不接受人工填数。

**顺带定位了复核里“组合运行停滞”的根因**：`locust/__init__.py` 在 import 时无条件 `gevent.monkey.patch_all()`，把整个进程的 threading/socket 换掉；pytest 里一旦先 import `tests/load/locustfile.py`（读 profile 表就会），同进程后续的 anyio/httpx/redis asyncio 用例就被破坏——表现为 `test_overload_burst_sheds_load_without_5xx` 里 100 个请求中 6 个既不是 201 也不是 429/503。修法是在 `tests/conftest.py` 顶部（任何 locust import 之前）设 `LOCUST_SKIP_MONKEY_PATCH=1`：pytest 进程不是压测进程，真跑压测时由 locust CLI 自己打补丁。效果：`tests/load` 由 **43s / 1 failed** 变为 **8s / 29 passed**，gevent 警告消失。

**边界**：仍**没有**在验收环境跑过，所以 SC-001–SC-005 依旧没有通过证据。

## §4 T035：只做了能证明的那部分

复核说“参数仍有可选形式”。已确认这不仅是风格问题，而是**跨租户泄露面**：

- `list_eval_cases(session, category, tenant_id=None)`：不传就**没有 WHERE**，列出所有租户的用例；
- `list_retrieval_items(session, enabled, tenant_id=None)`：同样；
- `create_eval_case(..., tenant_id=None)`：可以写出 tenant 为空的孤儿行。

改法：`tenant_id` 改成 keyword-only **必填**，配合 `_require_tenant()` 在空值时抛 `TENANT_REQUIRED`（403），`routes_eval.py` 的三处调用同步传入。证据：`python -m pytest tests -k "eval or tenant or isolation"` → **101 passed / 34 skipped**（skip 是需要 PG 的套件）。

**没做的**：`eval_service.py`（798 行）与 `knowledge_base_service.py` 仍然是同步 `Session` 权威读写，不是 async repository。这次只封住了“静默跨租户”，没有迁移执行模型；复核对该项仍应判 `[~]`。

## §5 文档与证据维护（复核列的 6 条）

1. `spec.md`：Status 增补“规格已冻结、实现验收未完成，勾选≠验收”，并指向本目录两份文档。
2. `artifacts/acceptance/SC-001-SC-016.md`：Electron E2E 由“未运行”改为**复跑失败**（0 passed / 8 failed，WDIO 建会话失败，附原始日志路径与 exit=1）；SC-001–SC-005 的“证据/原因”栏改成当前代码事实（分流、逐帧计时、并发门禁已实现但未运行）。
3. 同一份 SC 报告的环境行：`Python 3.13.9` → **Python 3.11.15（conda policyflow）**，并注明以 `artifacts/acceptance/backend/env.json` 原始记录为准。
4. 原始日志可归档性：`*.log` 被全局忽略，异机 checkout 取不到 Stage 8 的 `e2e-full.log` / `accessibility.log`。本轮未改 `.gitignore` 策略（改动会波及别的日志约定），改为**在文档里指明外部证据位置**并在 §7 记成待办。
5. `tests/load/test_acceptance_profile.py` 之前是 untracked：本轮它已扩充并被 `tests/load` 全集覆盖，但**仍需 `git add`** 才能进远端/CI（见 §7）。
6. T154 的旧全量记录（626 passed / 196 skipped / 1 failed）保持原样，不因 R40 复跑单条用例就改写成“全绿”。

`docs/08` §10.1 的 T153 / T154–T155 两行同步更新。

## §6 仍未完成的（与复核结论一致，不缩小）

- T053 单图收敛（§1）。
- T035 的 async/tenant-aware repository 全量迁移（§4）。
- 验收环境实跑：分布式 Locust、1,000 会话 / 1,000 SSE / 30 分钟 soak。
- 真实 Electron 联调：当前 E2E 连会话都建不起来；需要桌面会话/显示服务，且要先确认是环境问题还是启动参数问题（日志只证明启动失败）。
- gVisor 真隔离 Job、真实签名成品、SC-010/011 真实被试、T149 三存储全量对照、T158 零使用窗口。

## §7 待办（下一轮可直接接）

1. `git add tests/load/test_acceptance_profile.py frontend/electron/main/api-proxy.test.ts` —— 两个新/改测试文件必须进版本库，否则 CI 看不到（本次未提交，代码仍在工作区）。
2. 决定日志归档策略：把关键 E2E 日志移出 `*.log` 忽略面（如 `artifacts/**/*.txt`），或在文档中固定外部证据地址与哈希。
3. T053 切片 1（`ChatTurnState` + 节点搬迁）开工前，先把 Chat/Eval 契约套件跑成基线数字并留档。

## §8 本轮验证记录

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/contract tests/eval tests/load tests/integration/test_entrypoint_conclusion_parity.py -q` | **497 passed / 23 skipped / 1 xfailed**（60s） |
| `python -m pytest tests -k "eval or tenant or isolation" -q` | **101 passed / 34 skipped**（150s） |
| `python -m pytest tests/load -q` | **29 passed**（8s；修复前同一命令 43s + 1 failed） |
| `npm --prefix frontend run test` | **48 files / 162 passed**（30s） |
| `npx vitest run electron/main/api-proxy.test.ts electron/main/run-mapper.test.ts electron/main/schemas.test.ts` | **18 passed** |

未跑：Electron E2E、生产构建、签名打包、分布式 Locust、任何需要 PostgreSQL / Milvus / MinIO / gVisor 的套件（skip）。

