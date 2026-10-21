# 企业级智能体重构：完成度复核（2026-10-21）

复核对象：本目录的 `spec.md`、`plan.md`、`tasks.md`，当前代码及 `artifacts/` 原始证据。代码基准：`master` / `34b063327e183392f51d017c59409130fb5062ae`。本次为状态审查，未修改产品实现或任务勾选。

## 结论

**主要模块已经实现，但企业级重构尚未完成综合验收，不能宣称 spec 已交付。** `tasks.md` 共 164 项，145 项 `[X]`、19 项 `[~]`，按任务数量计算为 **88.4% 标记完成**。这不是工作量加权进度，也不是需求验收通过率；剩余项包括架构收敛、真实桌面联调、容量与隔离验证，不能视为简单收尾。

现有 SC 报告为 6 pass / 2 partial / 8 inconclusive。这些 verdict 必须连同其测试环境和范围阅读：例如安全与恢复的局部测试通过，不能推导所有生产路径、真实 gVisor 或 1,000 并发均已通过。

## 任务清单快照

| Phase | 范围 | 标记完成 / 总数 | 部分完成 |
|---|---|---:|---|
| 1 | 容量基线与基础设施骨架 | 15 / 15 | 无 |
| 2 | PostgreSQL、身份、租户基础 | 20 / 21 | T035 |
| 3 | 可信编排 | 17 / 18 | T053 |
| 4 | 持久任务、配额、SSE | 13 / 18 | T063、T069–T072 |
| 5 | Milvus、对象存储、版本核对 | 16 / 16 | 无 |
| 6 | 文件沙箱、审批与提交 | 19 / 21 | T092、T098 |
| 7 | Electron 安全外壳 | 12 / 13 | T121 |
| 8 | 工作流界面 | 14 / 15 | T137 |
| 9 | 管理员治理 | 11 / 11 | 无 |
| 10 | 综合验收与旧路径退出 | 8 / 16 | T149、T152、T154–T156、T158–T160 |

`[~]` 同时包含部分实现、未运行和被前置条件阻塞，不能当成已完成。Phase 4 部分说明仍引用早期已被后续轮次关闭的缺口，需要按当前代码重新逐条核销。

## 已有实证的能力

- 记忆服务已改为 async，并要求显式 tenant；T035 的 memory 子范围有 PostgreSQL 验证记录。
- 持久任务、outbox、配额、SSE 生命周期已实现，Stage 4 保存过 PostgreSQL / Redis / RabbitMQ 的分项运行证据。
- Stage 5 保存过真实 PostgreSQL / Milvus / MinIO 版本更新、物理删除和故障核对证据。
- Stage 6 的 PostgreSQL 测试记录为 157 passed；报销审批后仅产生一次 `status=mock` 提交，未批准、撤权、stale digest 等路径被拦截。**提交目标仍为 mock。**
- Stage 7/8 曾记录真实 Electron 安全和工作流 E2E 通过；Stage 8 原始日志当前本地存在，记录 8 spec / 33 tests，通过时连接的是确定性 stub 后端。
- 管理员治理已实现，US5 的原始记录覆盖角色撤销、审计脱敏、租户可见性及核对恢复。
- 检索兼容性曾用 BM25、N=50、200 干扰文档验证；该证据是检索兼容性，不代表完整答案一致性或企业容量。另一个 Hybrid 记录仅 N=3、2 干扰文档，应保留小样本边界。

## 需要优先处理的实际缺口

### 1. 单一编排尚未收敛（T053 / FR-001）

`backend/app/graph/route_adapter.py` 的真实 Chat/Eval 入口仍委托 `send_chat_message` / `iter_chat_events` / `execute_eval_run`，最终执行 `AgentPipeline` 驱动的 `pipeline_graph.py`。文件/授权运行另有 `builder.py` 的持久图。

因此目前是 **Chat/Eval 共用一张图，文件流程使用另一张持久图**；不是所有入口已经迁到同一 GraphService。两者共享证据门语义的局部测试通过，也不能代替单一编排交付。下一步应移植完整输出契约并验证真实入口，随后才具备旧路径退出的技术前提。

### 2. Electron 与真实后端的启动运行协议不一致

`frontend/electron/main/api-proxy.ts:171` 的 `startRun` 将 `input`、`knowledge_base_ids`、`idempotency_key` 放在请求体。真实后端 `backend/app/api/routes_runs.py:151` 接受 `kind`、`payload`、`priority_lane`、`max_attempts`，并在第 180 行要求 `Idempotency-Key` HTTP 头。

当前代理未传此头；按后端代码会返回 400。即使补头，`input` 也没有映射到 `payload`。这解释了为什么 **stub 工作流 E2E 通过不等于真实后端联调通过**。应先对齐协议，再验证 Electron →真实 API →任务→事件→审批的闭环。

### 3. 验收压测还有代码缺口，不能仅等待环境（T153 / T156）

直接调用当前 `CapacityUser.request_workflow`，profile=`acceptance` 时实际仅调用 `chat`：`tests/load/locustfile.py:364` 的分发表只识别 `sse/file/isolation`，没有消费 `workflow_weights`。

`_sse` 在 `response.text` 整段读取结束后才记录 `sse_first_event_ms`，因此该值是整段响应结束时延，不能用于证明 SC-004 的首阶段时延，也无法据此证明稳定保持 1,000 SSE。

`evaluate_acceptance` 仅核对 7 个指标，没有检查实际 `active_sessions` / `sse_streams`。本次直接复现：只提供吞吐、延迟、失败率、清理、soak、RSS 的合格数字，**缺少会话数与 SSE 并发数仍返回 pass**。

此外当前负载用户默认固定选择同一 tenant index，登录未发送 `tenant_code`，隔离请求只读取知识库列表；还不足以执行 spec 要求的多租户对抗验收。

应先实现真实负载分流、逐帧 SSE 计时、实际并发采集、身份切换和隔离断言，再运行验收环境下的分布式 Locust。SC-001–SC-005 目前没有通过证据。

### 4. 基础服务迁移尚未完整完成（T035）

当前 `memory_service` 已 async；`eval_service.py` 与 `knowledge_base_service.py` 仍存在同步 `Session` 权威读写。Eval 已增加部分 tenant 参数，不能继续引用早期“0 处 tenant 感知”的测量作为当前事实；但参数仍有可选形式，完整 tenant-aware async repository 迁移尚未完成。

该项位于 Foundation，后续 Phase 已推进并不消除其架构前置缺口。

### 5. 仍缺真实环境与用户验证

- gVisor：T092/T098 验证了 manifest 控制项与渲染，未真起隔离 Job 观测运行时边界。
- Electron：Stage 10 较新日志记录 WDIO 创建会话失败（`DevToolsActivePort file doesn't exist`，0 passed / 8 failed）；该日志证明启动失败，不能仅凭报错断定唯一原因是没有桌面会话。
- 签名发布：T121 的签名门已实现，尚缺真实签名成品验证。
- 目标员工研究：SC-010/011 没有真实被试记录；SC-012 的管理员新增页面还需补齐界面验收。
- 数据切换：T149 仍缺同一生产副本的 PG / Milvus / MinIO 全量对照与恢复点；T152 消费者扫描仍有引用，未满足 destructive contract 门槛。
- 旧路径退出：观察窗口为 2026-10-18 至 2026-11-18，截至本次复核未结束；本地 `legacy-usage.json` 不存在，不能将未测量解读为零使用。到日期也不会自动满足门槛，仍需完整部署观测与迁移验收。

## 文档与证据维护问题

1. `spec.md` 仍为 Draft，需求质量清单全勾选表示规格编写检查，不能当实现验收证据。
2. `artifacts/acceptance/SC-001-SC-016.md` 仍写 Electron E2E 未运行；R40 和 `quickstart-validation.md` 已记录运行失败，需要同步。
3. SC 报告写 Python 3.13.9；同目录 `backend/env.json` 实际为 conda policyflow 的 Python 3.11.15，应以原始环境记录为准。
4. Stage 8 的 `e2e-full.log` / `accessibility.log` 本地存在，但被全局 `*.log` 忽略且没有被 Git 跟踪；异机 checkout 无法取得这些原始日志。关键证据应使用可归档路径或明确的外部证据存储。
5. `tests/load/test_acceptance_profile.py` 在本次复核开始时已经是 untracked；本机执行通过不代表远端仓库或 CI 已有该测试。
6. T154 旧全量记录是 626 passed / 196 skipped / 1 failed；R40 修正不可达 PG 的测试分类并只复跑该用例为 skipped，不能把旧全量报告直接改写为最新全绿。

## 本次实际验证

- 后端定向测试：**102 passed**，覆盖退出门禁、数据比对 preflight、graph state、graph telemetry、确定性入口权限/序列、证据门、文件路径逃逸、沙箱 manifest、验收阈值逻辑。原始输出：`artifacts/review/2026-10-21/targeted-pytest.txt`。
- 真实 HTTP Chat/SSE 的确定性结论一致性测试：**1 passed**；原始输出：`artifacts/review/2026-10-21/route-parity-pytest.txt`。范围是确定性检索/模型替身，不包括真实 provider 或文件图收敛。
- `npm --prefix frontend run test`：**47 files / 155 passed**，退出码 0，本次工具输出记录；未重跑 Electron E2E、签名打包或生产构建。
- 压测分流与缺并发指标仍 pass 的两个问题通过直接调用当前函数复现；没有运行高并发或调用付费模型。
- 一次将 Locust 导入与 HTTP parity 放在同一 pytest 进程的组合运行停滞，已停止；拆开后两组分别通过。组合输出出现 gevent monkey patch 警告，尚未调查停滞根因，不能算组合套件通过。

后端输出 SHA-256：

| 文件 | SHA-256 |
|---|---|
| targeted-pytest.txt | D34355DD84BCF2CEBE5D11A5E24004601DEC8D88DFDA2438FD859EB893141274 |
| route-parity-pytest.txt | CB8E0CF10D8BAEC719456A4E6CDB1666227790D985804228C4ED19C760A06E35 |

## 建议推进顺序

1. 对齐 Electron 的真实 API 协议，补真实后端工作流联调；修正验收压测分流、SSE 计时和实际并发判定。
2. 完成 T035 的剩余服务迁移与 T053 的单图收敛，复核 Phase 4 的剩余逐项门禁。
3. 在完整企业栈和 gVisor 环境运行功能、安全、恢复、跨存储及分布式容量验收，保留同一环境的原始记录。
4. 补真实 Electron、签名和员工可用性验证，同步 SC verdict 与文档。
5. 在数据核对、恢复点、能力对等和完整零使用窗口全部满足后执行旧路径退出。

当前可描述为：**核心模块与开发/演示能力已较完整，企业容量、统一生产编排、完整桌面联调和最终迁移验收仍在推进。**
