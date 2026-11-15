---
status: issues_found
review_date: 2026-11-15
baseline_commit: 21a7c197655e57d884022d453da9f7551435d31d
scope: current_project_readiness
findings:
  p1: 4
  p2: 1
---

# 项目现状复核（2026-11-15）

> 修复状态：本报告保留修复前发现与基线。R1–R5 后续处理、最新质量门和仍待部署的边界见 [同日修复报告](repair-report-2026-11-15.md)。

**结论：Web 端核心 RAG 演示已形成完整功能链，企业化重构尚未交付完成。当前应优先修复租户清理边界和真实 worker / Electron 接线，再继续统一编排与企业验收。**

本次核对任务清单、现有验收材料和关键调用链，重新运行后端全量、前端静态检查/单测、生产构建与浏览器 E2E，并用独立临时 SQLite 数据库及离线 SSE 报文复现缺陷。未修改业务代码、未推送；已有未跟踪数据库和脚本未清理。测试更新的旧 parity 产物已另存本轮证据目录并恢复原文件。

当前任务表共 **164 项：145 `[X]`、19 `[~]`、0 `[ ]`**。这是任务标记统计，不是产品完成率。若 `[X]` 对应组件只在注入依赖或 stub 下通过，仍不能推导真实用户入口已贯通。

## 1. 需要优先修复的四个问题

### R1 · P1：评估数据清理可以修改其他租户的数据

- 入口：`backend/app/api/routes_eval.py:153`；实现：`backend/app/services/eval_service.py:194`。
- `post_eval_cleanup` 只要求 `kb_admin/sys_admin`，未向服务传入当前租户。服务无条件扫描知识库、检索评测项、EvalCase 和已软删除文档，并执行删除/禁用。
- **本次真实 API 复现**：在隔离库中创建租户 A 管理员与租户 B 的评测数据；使用 A 的有效 token 调用 `POST /api/eval/datasets/cleanup`，返回 200；B 的检索用例被删除，B 的 EvalCase 从 enabled=true 变为 false。
- 这证明当前应用层缺少租户限定。复现环境是 SQLite；没有把该结果冒充 PostgreSQL RLS 穿透。生产 RLS 是额外防线，不能替代请求和服务层的租户条件，尤其当前同步 `SessionDep` 没有建立请求租户上下文。
- 修复验收：服务强制接收认证派生的 tenant_id；所有读取、删除、禁用、统计限定同一租户；双租户 API 回归证明 B 的行与状态完全不变。

### R2 · P1：桌面端 chat / file_workflow 已入队，但没有业务处理器

- 入口：`backend/app/api/routes_runs.py:176`；处理器：`backend/app/jobs/runner.py:157`、`backend/app/jobs/worker.py:48`。
- 桌面聊天发送 `kind='chat'`，文件流程使用 `file_workflow`。`POST /api/v2/runs` 接受这些 kind 并写入 DurableJob；默认 registry 只有 `document_index`、`eval_run`，CLI worker 仅再增加 `recovery_probe`。
- **本次复现**：两个 kind 均获 HTTP 201；交给生产同款默认 registry 和 consumer cycle 后，均进入 `terminal_failed / NO_HANDLER`。不启动消费者则只停在 queued。
- `app.state.graph_service` 的存在不能代替 handler 接线。当前文件工作流的服务级组合测试，也不能证明桌面 → API → worker → graph 已贯通。
- 修复验收：接通携带认证身份与工作流上下文的业务 handler，拒绝未知 kind；真实后端跑通问答结果、文件草稿、审批暂停及恢复，不能只断言入队成功。

### R3 · P1：CLI worker 缺业务依赖，索引/评估任务空执行却记为成功

- 构造：`backend/app/jobs/worker.py:59` → `backend/app/jobs/consumer.py:156`；空返回：`backend/app/jobs/runner.py:107`、`:130`。
- CLI `build_app()` 未提供业务 `JobContext`；默认 context 只有 async engine/event stream，没有 LightRAG adapter 或 app_state。索引 handler 在 adapter 缺失时直接 return；评估 handler 在 app_state 缺失时直接 return。runner 随后正常 complete，发布成功事件。
- **本次复现**：默认 consumer 接收 `document_index` 与 `eval_run`，即使 payload 为空，仍得到 `succeeded`，没有触发本应读取 payload 的业务逻辑。这不是“业务逻辑执行后失败另记领域状态”，而是业务根本没执行。
- 修复验收：为独立 worker 显式装配同步/异步数据库访问与业务服务；依赖缺失应启动失败或任务显式失败；用真实索引/评估状态变化证明成功，不能仅证明恢复探针能跑。

### R4 · P1：Electron SSE 消费端与后端报文契约不一致

- 桌面解析：`frontend/electron/main/api-proxy.ts:268`、`:286`；后端帧：`backend/app/sse/endpoint.py:46`；前端终态：`frontend/src/desktop/use-run-events.ts:21`。
- 桌面以 `\n\n` 分帧，但当前 `sse-starlette` 默认输出 `\r\n\r\n`，完整真实事件因此不能被切出。
- 即便只把换行归一化，解析器只读 `data:`，丢弃 `event:` / `id:`。后端把类型和游标放在 SSE 字段里，payload 不一定含 event_type/event_id，因此映射成 `unknown` / 空 ID。
- 还有独立的终态命名差异：后端发送 `run.succeeded` / `run.cancelled`，hook 只识别 `run.completed` / `run.failed`。
- **本次离线跨语言复现**：使用实际 Python `_frame` + `ServerSentEvent.encode()` 生成成功事件，交给实际 `ApiProxy.streamRunEvents`；原始报文产生 **0 条事件**，仅归一化换行后产生 **1 条 unknown、空 eventId 的事件**。没有使用自编的解析器替代产品代码。
- 修复验收：统一 LF/CRLF 分帧、SSE 元数据、payload、终态和断线游标契约；直接消费真实后端编码的报文，并覆盖成功/取消/重连。Electron 的宿主启动问题与这些应用层缺陷应分开追踪。

以上前三项复现见 [reproduce_findings.py](../../artifacts/review/2026-11-15/reproduce_findings.py) 与 [原始输出](../../artifacts/review/2026-11-15/reproductions.txt)；R4 见 [reproduce_sse.mjs](../../artifacts/review/2026-11-15/reproduce_sse.mjs) 与 [原始输出](../../artifacts/review/2026-11-15/sse-reproduction.txt)。脚本里的断言刻意确认本轮发现的错误行为，不能当作修复后的正确性测试。

## 2. 本轮验证结果

| 检查 | 当前结果 | 含义 |
|---|---|---|
| `python -m pytest -q -ra --tb=short` | **967 passed / 197 skipped / 0 failed**，309.10 秒 | 原有套件通过；PG/对象存储/消息队列等集成未全量实跑；上述接线缺陷未被原有断言覆盖 |
| 前端 typecheck | 通过 | 类型检查通过 |
| 前端 lint | 通过 | ESLint 无错误 |
| 前端全量 Vitest | **161 passed / 1 failed**，48 文件 | 聊天页“无可靠证据”可见性断言失败 |
| 聊天页单独复跑 | **4 passed / 2 failed** | 原失败仍在，另出现引用区查找失败；存在异步渲染/查询稳定性问题，尚未归因成确定的用户界面缺陷 |
| 生产构建与产物检查 | 通过 | `npm run e2e:production` 的 build 阶段成功 |
| 生产浏览器 E2E，默认 10 workers | **14 passed / 5 failed** | 知识库、聊天草稿、FAQ、Skill、员工登录相关等待超时 |
| 同一生产 E2E，`--workers=1` | **19 passed / 0 failed**，55.1 秒 | Web 核心流程可串行复现；不能据此把默认并行失败抹去或宣布并发能力达标 |
| 后端 Ruff | **29 errors** | 以 unused import、导入排序、枚举/异常命名为主；属于工程清理项 |

**R5 · P2：前端默认质量门尚不稳定。** 单测和默认并行 E2E 目前不能按 README 的“全绿”照抄。本轮用串行复跑区分了流程可用性与并行问题，但没有确定其根因是 SQLite 写竞争、共享测试数据、资源压力还是应用阻塞。后续应保留失败时的请求时序和后端日志定位，不能仅加长超时或降低并发后宣布修好。

浏览器 E2E 使用真实 FastAPI + 隔离 SQLite，但检索/LLM 是 `frontend/scripts/start-e2e-backend.py` 中的确定性替身。因此本轮证明 Web 页面与 API 流程，**不产生真实检索质量、真实模型能力、PostgreSQL 隔离或容量结论**。本轮未重跑 Electron GUI、分布式容量、真实 provider 付费请求、gVisor 集群和签名打包。

原始日志均位于 [本轮证据目录](../../artifacts/review/2026-11-15/)。

## 3. 已做成的部分

| 范围 | 可以支持的结论 |
|---|---|
| Web 制度问答、引用、短跟进、历史与反馈 | 有完整实现，串行生产 Web E2E 跑通；真实模型质量仍需单独评测 |
| 记忆 | 当前 memory_service 已通过强制 tenant 参数的异步 repository 读写；四层非权威记忆、管理接口和 query rewrite 已有实现与测试 |
| 知识库与评估 | 上传/索引、CRUD 金标导入、Hit@K/MRR、负样本口径和导出均有实现；Web 纵向流程可跑；异步服务迁移和清理权限边界仍需完成 |
| Skill/Tool/MCP | 有真实注册/调用和协议实现；企业连接器、外部提交依然明确为 mock |
| 企业基础组件 | 多租户模型、迁移、任务/outbox、版本化材料、审批摘要/幂等、核对、审计、配额与 SSE 都有代码和相应测试，不能据此等同全部用户入口可用 |
| 管理 UI | 治理、运行审计、一致性处理页面及浏览器无障碍测试本轮串行通过 |

## 4. 19 项部分完成任务，按交付依赖整理

| 剩余方向 | 任务 | 还缺什么 |
|---|---|---|
| 服务层迁移 | T035 | Memory 已迁；KB 仅四个只读端点切到 async repository，其余写入/文档路径及 Eval 服务仍有同步 Session；先补齐 R1 的租户边界 |
| 单一编排 | T053 | Chat/Eval 仍经 `AgentPipeline` + `pipeline_graph`，文件流程使用另一张图；共享节点和 shadow 没有完成生产编排收敛，事件/skill parity 尚未达到切换条件 |
| 持久任务与容量闭环 | T063、T069、T070、T071、T072 | 不少历史子缺口已补，但本轮发现 worker 业务依赖与 handler 接线缺口；需要真实业务消费、取消/重试/恢复/配额及 SSE 联合验证，之后再做负载验收 |
| 沙箱内核隔离 | T092、T098 | manifest/本地校验已有；gVisor Job 在真实集群执行、网络与资源边界验证尚缺 |
| 桌面与用户验收 | T121、T137、T155 | R2/R4 的真实后端集成、Electron 全量 GUI 复跑、真实签名包、目标员工可用性研究 |
| 数据权威迁移 | T149、T152 | PostgreSQL / MinIO / Milvus 全量对照、恢复点、消费者清单与受控 contract；不能现在直接删旧字段 |
| 最终质量与容量验收 | T154、T156 | 后端无依赖子集已绿但仍有 197 skip；独立环境的 1000 会话/1000 SSE/200 RPS/30 分钟 soak 尚无本轮验收结果 |
| 旧路径退场 | T158、T159、T160 | 使用量证据和替代入口都要满足；现有窗口到 **2026-11-18**，本次审查日 **2026-11-15** 尚未结束。即使到期也不等于可以删除，必须实测零使用且替代入口可用 |

真实企业连接器/SSO 等早期路线图扩展，应与当前承诺的交付缺口区分：mock 本身符合已声明边界，不因存在 mock 就判假实现；缺失的真实 worker 接线则会直接破坏当前声称支持的流程。

## 5. 文档与验收口径需要调整

- 旧综合验收总账记录 **7 pass / 1 partial / 8 inconclusive**，这是历史 verdict，本轮没有把它重新全部验过。尤其 SC-007 的“越权/跨租户 0 泄露”不能再泛化到所有接口：R1 提供了评估写操作的反例，需修复后补全边界验收。
- `tasks.md` 保留大量历史追加段落，例如旧段落说 memory 仍未租户化，而当前实现已经迁移。应增加简明的当前状态表，历史正文显式标记已被哪次变更取代。
- `docs/05` 仍写 137/164，与当前 145/164 不一致；README 的前端全绿状态也需要注明历史日期与运行方式。
- 不应把“桌面现在跑不起来”完全归为宿主环境：R2/R4 是本轮独立于 GUI 启动限制复现的应用缺口。
- 默认开发启动用 SQLite，企业设计使用 PostgreSQL；两套模式要分别给出验证结果，不能混成一个“已通过企业验收”的结论。

## 6. 建议完成顺序及退出条件

1. **先修 R1**：双租户清理测试证明其他租户完全不变，补同类批处理入口核对。
2. **接通 R2/R3/R4**：真实业务任务必须真正执行；桌面接实际后端事件，问答和文件审批各有一条不用 stub 后端的纵向验证。
3. **恢复稳定质量门**：处理聊天单测与默认并行 E2E 失败；清理 Ruff 基线，固定环境和命令，并保留原始证据。
4. **完成 T035/T053**：服务迁移和编排收敛沿可回归切片推进；以相同输入、证据、完整输出/事件契约验证 parity。
5. **做企业栈验收**：PG/RLS、对象与向量核对、broker 实际消费与恢复、gVisor、容量、签名与用户研究分别交付证据。
6. **最后退旧路径**：只有真实替代流程、迁移/恢复证据、完整窗口零使用都满足，才执行 contract 和 UI 退场。

如果近期目标是面试展示，优先交付 1–3，并固定一条 Web 演示路径与一份带检索策略、N、干扰文档设置的真实评测报告。展示时可说“已有企业化基础组件，正在完成真实接线与部署验收”，不应说“企业级并发和桌面全流程已经全部完成”。

## 7. 复现命令

在项目根目录使用 conda `policyflow` 的 Python；本机解释器是 `E:/Coding/Anaconda/envs/policyflow/python.exe`。

```powershell
python -m pytest -q -ra --tb=short
python -m ruff check backend/app --output-format concise
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend test
Push-Location frontend
npx vitest run src/features/chat/chat-page.test.tsx
Pop-Location

$env:POLICYFLOW_PYTHON = (Get-Command python).Source
npm --prefix frontend run e2e:production
Push-Location frontend
npx playwright test --config=playwright.production.config.ts --workers=1
Pop-Location

python artifacts/review/2026-11-15/reproduce_findings.py
node artifacts/review/2026-11-15/reproduce_sse.mjs
```

E2E 会重建项目内专用的 `frontend/.tmp/e2e-backend` 测试运行目录；8000/4173 端口应未被业务服务占用。复现缺陷脚本不访问业务数据库。
