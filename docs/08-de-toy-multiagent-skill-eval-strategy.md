# 08. 去玩具化 / 多智能体 / Skill·Tool·MCP / Eval 改造总策略

版本：v1.7  
日期：2026-07-20  
状态：**Phase 0–3 主线与加分项已基本落地**（见 §10）；TurnState 错误账本已接入；可选 Critique→Improve 反思闭环已落地  
读者：后续实现的自己 / 评审 / 面试准备  
前置文档：[`01-architecture-design.md`](01-architecture-design.md)、[`04-ai-pipeline-rag-eval-design.md`](04-ai-pipeline-rag-eval-design.md)、[`05-development-roadmap.md`](05-development-roadmap.md)、[`09-interview-demo-script.md`](09-interview-demo-script.md)

> 本文合并两轮审阅结论：  
> 1）整体仍偏玩具，编排双写，Router/Skill/Compliance 名不副实；  
> 2）Skill / Tool / MCP 需要诚实实现，Eval 需可导入 CRUD 语料并展示 Hit@K/MRR（RAGAS 可选），服务面试演示。  
> **实现前先读本文；实现后更新「落地状态」一节。**

---

## 0. 一句话定位

PolicyFlow AI 应做成：

**统一编排的企业政策 RAG 对话系统**  
— 主路径是 **tool-using Answer Agent**；  
— Skill = 业务规程；Tool = 可审计原子能力；MCP = 外部协议适配；  
— Eval = 可导入外部金标语料，主指标 Hit@K/MRR，页面可点可导出。

**不是**：六个关键词 if 拼成的「多智能体平台」，也不是只有壳的 Skill/MCP 展示页。

---

## 1. 代码现状评分

### 1.0 审阅基线（2026-07 改造前）

| 子系统 | 等级 | 说明 |
|---|---|---|
| Hybrid RAG（LightRAG + BM25 + RRF） | 强 MVP | 真检索；rerank 不可用；LightRAG 分有合成衰减 |
| 多层 Memory | 强 MVP | load/writeback/compress/search 真；embedding 存 SQLite JSON |
| Answer 生成 | MVP | 真 LLM；证据绑定偏 prompt；无证据仍可能软答 |
| 命名 Agent 层（Router / Skill / Compliance） | 玩具 | 关键词/空壳 |
| Skill handlers | 玩具 | 固定三步 / 回显 / 按句号切 |
| Tool registry + audit | 脚手架可用 | draft/memory 真；**chat 不调用** |
| MCP | Mock | 非 mock 直接 503 |
| Tool-use loop | 缺失 | 无 function calling |
| 编排 | 双写 | chat 内联 stages ≠ `AgentPipeline.run`（eval 用） |
| Hit@K / MRR 公式 | 真 | `backend/app/evals/retrieval_metrics.py` |
| RAGAS | 空壳 | 恒 `skipped` / `evaluator_not_configured` |
| Eval 数据集 | 手工逐条 | 无 CRUD 批量导入 |

### 1.0b 改造后快照（2026-07-14）

| 子系统 | 等级 | 说明 |
|---|---|---|
| Hybrid RAG + 本地 rerank | 强 MVP | 真 hybrid；可选 `local_lexical_fusion` rerank（非 cross-encoder） |
| 多层 Memory | 强 MVP | 同前 |
| Answer + tool loop | 强 MVP | function-calling 工具环；Skill 结果可回灌 |
| Router / Skill / Verifier | MVP+ | LLM 结构化 Router；证据 Skill；规则 Verifier + claim 词重叠门 |
| Tool / MCP | MVP | 真 tool audit；MCP stdio/http client + mock 标注 |
| Eval | 强 MVP | CRUD 导入、Hit@K/MRR/HitAll、多策略、导出、可选 RAGAS |

### 1.1 关键绝对路径

```
backend/app/agents/           # 命名 agent；多数非真 agent
backend/app/services/chat_service.py   # 生产编排（内联 stages）
backend/app/agents/pipeline.py         # eval 用 pipeline（与 chat 易漂移）
backend/app/skills/                    # registry + mock handlers
backend/app/tools/                     # registry + builtin tools
backend/app/mcp/                       # mock manager only
backend/app/evals/                     # runner + metrics + ragas stub
backend/app/services/eval_service.py
frontend/src/features/evaluation/      # 评估中心页
```

### 1.2 必须修的工程债

1. **chat 与 `AgentPipeline` 双写** → 统一 `Orchestrator`  
2. **diagnostics 伪造** `skill.suggest` / 假 tool trace → 只报真实调用  
3. **LightRAG score 合成衰减** → 标注 synthetic，评测别当模型相关分  
4. **query rewrite 纯启发式** → 升 LLM 结构化或并入 Router  

---

## 2. 去玩具化原则

### 2.1 命名诚实

- 没有独立目标、没有决策或工具环的模块，**不要叫 Agent**  
  - `RetrievalAgent` → 视为 `RAGService` 门面，文档中称 Retrieval Service  
  - 关键词 `ComplianceAgent` → 并入 Verifier / `ComplianceGate`  
- 允许称 Agent 的节点见 §3

### 2.2 证据绑定要硬

| 机制 | 现状 | 目标 |
|---|---|---|
| 无证据拒答 | 仍调 LLM + 免责声明 | 默认 hard refuse；可配置 soft |
| `NO_RELIABLE_EVIDENCE` | 不 fail compliance | 无证据时 `passed=false`（可配置） |
| confidence | `0.6 + 0.05 * n` | Verifier/覆盖度；去掉纯长度公式 |
| citations | 全量 evidence 列表 | 与答案 `[n]` 对齐校验 |
| 政策事实写入 preference | 有关键词拦截 | 保留并加强 |

### 2.3 明确不做

- 再堆「只有关键词」的 Agent  
- 为页面好看伪造 tool/skill 轨迹  
- mock MCP 画成「已真实发送邮件/飞书」  
- 用 `80000_docs` 正文当 QA 金标报 Hit@K  
- 编排双路径继续分叉  
- 用 RAGAS 分数冒充检索指标  

---

## 3. 多智能体：诚实落点

### 3.1 目标拓扑

```
                    ┌─────────────┐
                    │  MemoryLoad │  service（extract 可后台 LLM）
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │   Router    │  LLM structured  ← 决策 Agent
                    │ domain/risk │
                    │ skill? tools? rewrite? │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
     RetrievalSvc    SkillExecutor      (skip)
     (非 agent)      (可选 Agent)
           └───────────────┬───────────────┘
                           ▼
                    ┌─────────────┐
                    │ AnswerAgent │  LLM + tool loop  ← 主 Agent
                    │ kb.search / skill.run /
                    │ draft.* / memory.* / mcp.call │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │ReflectionLoop│  可选 · 高风险触发
                    │ Critique→Improve │  双 prompt · max 2 轮
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │  Verifier   │  规则 Compliance  ← 质量门（非 LLM）
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │MemoryWriteback│
                    └─────────────┘
```

并行仅保留检索内部 hybrid 的 `asyncio.gather`；**不做**多角色群聊式 multi-agent。

### 3.2 节点表

| 节点 | LLM | 工具 | 叫法 | 现状 → 目标 |
|---|---|---|---|---|
| Router | 是（JSON） | 否 | Agent | 3 个风险词 → 结构化路由 |
| Retrieval | 否 | 否 | Service | 保持；去掉 Agent 包装感 |
| SkillExecutor | 是 | 可 | 可选 Agent | suggest-only → 真执行 |
| Answer + Tools | 是 | 是 | 主 Agent | 单次 complete → tool loop |
| ReflectionLoop | 是（双 prompt） | 否 | 可选质量环 | 无 → Critique→Improve · 硬轮次 · 仅高风险 |
| Verifier / Compliance | 规则 | 否 | Gate | citation/拒答/数值/claim 词重叠门 |
| Memory | 部分 | 否 | Service/后台 | 已较强，保留 |

### 3.3 面试安全表述

> 不是 CrewAI 式多角色群聊。是 **Supervisor 式流水线**：Router 结构化路由，Answer 做 tool-using 主 agent，Skill/Verifier 为可选专职节点；高风险回答可走 **Critique→Improve** 双 prompt 闭环（硬轮次上限，非 peer 互评群聊）；检索与记忆是服务。可观测、可评测、可审计。

### 3.4 多智能体「加在哪」（清单）

1. **Router** — 关键词 → LLM 结构化（domain, risk, task_type, need_skill, tool_hints, rewrite, **complexity, plan_steps**）  
2. **Answer tool loop** — 最大收益的 agent 化  
3. **SkillExecutor** — 真执行 registry  
4. **Verifier** — citation / 无证据拒绝 / 合并 compliance（规则门）  
5. **（可选）QueryRewrite** — 并入 Router 或独立一小步  
6. **渐进式多步规划（L1）** — Router 输出 `complexity/plan_steps`；`plan_normalize` 服务校验/用户步骤优先；Pipeline 驱动同一流水线并 SSE `plan`/`plan_step`；**不是**开放 Planner Agent，也不是 peer multi-agent  
7. **ReflectionLoop（可选）** — 仅高风险（multi_step/branched、risk、skill 成功清单、低置信）触发；**Critique 只找问题（6 维度 + PASS 出口）**，**Improve 只按批注改**；`CHAT_REFLECTION_MAX_ROUNDS=2` 硬停；Eval 默认关；无证据 hard refuse 永不反思  
8. **不要**并行六个 agent 互聊  

### 3.5 渐进式规划（L1 / L2 / L2.5 落点）

| 层级 | 行为 | 状态 |
|---|---|---|
| L0 单路径 | Router → Retrieval → Skill? → Answer | 已有 |
| **L1 Router 计划** | 简单题 simple；复杂题/用户编号步骤 → plan checklist；同流水线解释执行 | **已落地** |
| **L2 PlanExecutor** | 按步真执行；`depends_on` 推断；**独立子任务同波并行**（`asyncio.gather`，多 retrieve 等）；证据 bag 累积；仍中心化 | **已落地** |
| **L2.5 ToT 选路 (HITL)** | `difficulty=branched` → 生成 2–3 条候选计划 → **用户必选** → 再 PlanExecutor；双请求不挂起 HTTP | **已落地** |
| L3 Worker 拆分 | 瓶颈环节专才 Worker | 未做 |
| L4 群聊 multi-agent | 禁止 | 禁止 |

**难度 → 推理模式（三档，诚实命名）**

| difficulty | reasoning_mode | 行为 |
|---|---|---|
| `simple` | `cot_direct` | CoT 直答；无/弱计划清单 |
| `multi_step` | `cot_steps` | CoT 分步；L1/L2 单链计划 |
| `branched` | `tot_select` | ToT 选路；多候选 + 用户选路后再执行 |

> 产品 ToT = **多候选计划 + 用户选路**，**不是**学术 Tree-of-Thoughts 搜索/ beam search；仍是中心化 Supervisor + PlanExecutor，**无 peer multi-agent**。  
> 用户已编号 `1. 2. 3.` 线性步骤 → 强制 `multi_step`，不升 `branched`。

**双请求 HITL（不挂起 HTTP）**

```text
Request A  POST /api/chat/stream  { question, … }
  Memory → Router → normalize
  if branched + hitl:
    generate 2–3 PlanOption
    persist pending on assistant Message.meta_json
    SSE: plan_options → final(status=awaiting_plan_selection) → CLOSE
  else:
    full execute → final(status=completed)

Request B  POST /api/chat/stream  { conversation_id, selected_option_id }
  load pending → validate option/TTL/owner
  PlanExecutor on chosen steps (skip re-route)
  final(status=completed); clear pending; AIQueryLog + memory writeback
```

Eval：`hitl=False` 自动选 `recommended`，从不暂停。

配置：
- `CHAT_PLANNING_ENABLED`、`CHAT_PLAN_MAX_STEPS`（默认 5）
- `CHAT_PLAN_EXECUTOR`（L2 开关，默认 true）
- `CHAT_PLAN_PARALLEL`（同波并行，默认 true）
- `CHAT_TOT_ENABLED`、`CHAT_TOT_AUTO_TRIGGER`（默认 true）
- `CHAT_TOT_MIN_OPTIONS` / `CHAT_TOT_MAX_OPTIONS`（2–3）
- `CHAT_TOT_PENDING_TTL_MINUTES`（默认 60）

并行判定（保守）：
- 显式 `depends_on` 优先；否则 skill/tool 依赖全部 prior retrieve；answer/verify 依赖 prior 生产步
- **不同 query 的 retrieve 互不依赖 → 同一 wave 并行**
- 相同 query 的 retrieve 串行去重；answer/tool/verify 永不与其它步同波

SSE：`plan` / `plan_step`（CoT 分步与 ToT 执行）+ `plan_options`（ToT 待选）  
代码：`plan_normalize.py`、`plan_branch.py`、`plan_executor.py`、Pipeline pause/resume、`chat_service` dual-request、前端路径选择卡片。
---

## 4. Skill / Tool / MCP：诚实实现

### 4.1 分层定义

| 层 | 定义 | 现状 | 目标 |
|---|---|---|---|
| **Tool** | 原子能力 + `ToolCallLog` | draft/memory 真；mcp 假；chat 不调 | function-calling 可调 |
| **Skill** | 多步业务规程 | 固定 mock；只 suggest | 证据 + LLM 结构化；可 `skill.run` |
| **MCP** | 外部协议适配 | 全 mock | 真 client；本地 server 真；企业可 mock 并标注 |

### 4.2 Tool

**保留**：`backend/app/tools/registry.py` 的 register / execute / logs。

**必做**：

1. `LLMService.complete_with_tools(messages, tools)`  
   - OpenAI-compatible `tools` / `tool_calls`  
   - 无 tool 时退回现有 `complete`  
2. Orchestrator 内 tool loop：max 3–5 轮、白名单、超时  
3. 新增内部 tool：  
   - `kb.search` → `RAGService.retrieve`  
   - `skill.run` → `SkillRegistry.run`  
4. SSE 真实事件：`tool_call` / `tool_result`  
5. **禁止** `ToolCallTrace(status="suggested")` 装饰性条目  

**已有真 handler**（可挂进 loop）：

- `draft.create` / `draft.update`  
- `memory.read` / `memory.write`（仅当前用户）  
- `mcp.call`（网关形态对，后端需真/mock 分支）

### 4.3 Skill

| Skill | 诚实行为 |
|---|---|
| `process_checklist` | question + evidence → 条件/材料/步骤/时限；无证据 `insufficient_evidence`，不编清单 |
| `policy_compare` | ≥2 段证据 → 维度对比表 + evidence index |
| `summary` | 证据/长文 → 要点 + 来源编号；禁止按句号切三句 |

触发方式：

1. Router 判定 `need_skill` → SkillExecutor 节点；或  
2. Answer 通过 tool `skill.run` 调用  

均写 audit（现有 `skill.run` audit 可复用）。

### 4.4 MCP

```
AnswerAgent
  → tool: mcp.call
    → MCPManager
      → MCPClient (JSON-RPC: initialize / tools/list / tools/call)
        → transport: stdio | streamable-http
          → 本地真 server（filesystem / fetch 等）
          → 企业连接器：integration_mode=mock，响应必须含 status=mock
```

规则：

- `integration_mode ∈ {stdio, http, mock}`  
- health：真 list tools；mock → `mock-healthy`  
- UI/日志禁止把 mock 画成生产成功  
- 飞书/邮箱等无租户密钥时 **诚实 mock**，不装已对接  

### 4.5 与多智能体的边界（面试常问）

- Tool / MCP **不是** Agent，是 AnswerAgent 的手  
- Skill 是 **可选专职节点** 或高阶 tool  
- 检索是 **Service**  

---

## 5. Eval：CRUD 数据 + Hit@K/MRR + 可选 RAGAS + 页面

### 5.1 指标

| 指标 | 位置 | 状态 | 目标 |
|---|---|---|---|
| Hit@K / MRR / Recall@K | `evals/retrieval_metrics.py` | 公式真 | 金标 id 对齐 + 批量跑 + 看板 |
| Answer keywords / citation 粗评 | `evals/eval_runner.py` | 弱但有 | 辅指标 |
| RAGAS | `evals/ragas_runner.py` | 空壳 | 可选真接；默认关；失败 `skipped+reason` |

主叙事：**检索金标 = Hit@K/MRR**；RAGAS = generation 辅指标，成本与不稳定性要说清。

### 5.2 外部数据集（本机路径）

```
D:\Coding\Code\Github\CRUD_RAG\data\
  80000_docs/*                      # 仅语料分片（新闻正文），不是 QA
  crud_split/split_merged.json      # 任务金标
    questanswer_1doc                # 主评测：questions + news1 + answers + ID
    questanswer_2docs / _3docs      # 多文档 QA（进阶）
    event_summary / continuing_writing / hallu_modified  # 非 Hit@K 主线
```

`questanswer_1doc` 字段要点：

- `ID`：稳定文档/事件 id → 导入为 `Document.external_id`（或等价 source key）  
- `news1`：金标正文  
- `questions` / `answers`：评测 QA  

**Hit@K 前提**：`retrieve` 返回的 `document_id` / 稳定 `source_id` 能匹配 gold 映射。  
**禁止**：只灌 `80000_docs` 却无 gold id 映射就报 Hit@K。

### 5.3 演示规模（默认不要上 8 万）

| 包 | 规模 | 用途 |
|---|---|---|
| Demo-S | 200–500 文 + ~100 QA | 本地 10–30 分钟，面试现场 |
| Demo-M | ~2k 文 + ~300 QA | 报告截图 |
| Full | ~8 万文 | 架构可扩展；默认不跑 |

### 5.4 导入 → 评测流水线

1. **Import corpus** → 目标 KB → `Document(external_id=CRUD.ID, body=...)` → 现有 indexing  
2. **Import QA** → `RetrievalEvalItem(query, relevant_document_ids=[mapped], knowledge_base_ids)`  
3. 可选 `EvalCase(question, reference/keywords from answers)`  
4. **Run** → `eval_types` 含 `retrieval`；`top_k_values=[1,3,5,10]`；strategy 可 A/B  
5. **看板** → MRR、Hit@K、Recall、latency；strategy 对比；bad case 下钻  
6. **导出** JSON/CSV  

建议 API（名称可微调）：

- `POST /api/eval/datasets/import`  
- `POST /api/eval/datasets/{id}/materialize`  
- 现有 run / list / detail 复用增强展示  

### 5.5 评估页（面试官可点）

文件：`frontend/src/features/evaluation/evaluation-page.tsx`

增强点：

1. 数据集导入向导（类型、采样 N、目标 KB、进度）  
2. 一键 Run（retrieval 必选；answer；RAGAS 可选；多 strategy）  
3. 结果看板（大数字 + 对比表 + case 列表）  
4. 导出报告  

### 5.6 RAGAS 诚实策略

- 默认 `enabled=false`  
- 依赖缺失 → `skipped` + `missing_dependency`（已有语义）  
- 真接时：faithfulness / answer_relevancy / context_precision（有 reference 时）  
- 仅在 Demo-S 子集上可选开启  
- **不得**在检索对比表里用 RAGAS 替换 Hit@K  

### 5.7 Eval 必须与线上同路径

`EvalRunner` 调 **同一 Orchestrator**（至少 retrieval + answer 与 chat 一致）。  
否则页面数字与产品行为是两套故事——面试一问就穿。

---

## 6. 统一主路径（实现后的唯一故事）

### 6.1 在线问答

```
User question
  → persist user message
  → MemoryLoad
  → Router (LLM structured)
  → RetrievalService (hybrid + real trace)
  → SkillExecutor? (real run + audit)
  → AnswerAgent
        ↺ tool loop: kb.search | skill.run | draft.* | memory.* | mcp.call
  → ReflectionLoop? (CritiqueAgent → ImproveAgent, max 2; high-stakes only)
  → Verifier / ComplianceAgent (rules: citations / refuse / warnings)
  → persist assistant + AIQueryLog + ToolCallLog
  → MemoryWriteback
  → SSE: stage / tool_* / diagnostics（全部真实）
```

### 6.2 离线评估

```
CRUD import → index → RetrievalEvalItem / EvalCase
  → same RetrievalService / optional same Answer path
  → Hit@K · MRR [+ optional RAGAS]
  → Evaluation dashboard + export
```

---

## 7. 分阶段落地

### Phase 0 — 地基（0.5–1 天）

- [x] 引入唯一 `Orchestrator`（或让 chat 只调 `pipeline.run` 并扩展 SSE 钩子）  
- [x] 删除伪造 tool/skill diagnostics  
- [x] 文档/注释：Retrieval 不作 Agent  

### Phase 1 — 面试主线（约 1–1.5 周）**【优先】**

- [x] `complete_with_tools` + Answer tool loop + 真实 `ToolCallLog`  
- [x] Skill handlers 证据绑定 + 真执行（router 或 `skill.run`）  
- [x] CRUD importer + `external_id` 对齐 + `RetrievalEvalItem` 批量创建  
- [x] Eval 页：导入 / Run / Hit@K·MRR 看板 / 导出  
- [x] 无证据 hard refuse 可配置 + Verifier 最小版（合并 compliance）  

### Phase 2 — 协议与路由（约 1 周）

- [x] MCPClient + stdio + ≥1 真 server；企业 mock 明确标注  
- [x] Router LLM 结构化（替换关键词）  
- [x] Eval 多 strategy 对比（BM25 vs Hybrid 等）  
- [x] Query rewrite 并入 Router 或独立 LLM 步  

### Phase 3 — 加分

- [x] RAGAS 真跑（可选开关）  
- [x] Verifier 加强（claim–evidence 词重叠门 + 引用/数值）  
- [x] `questanswer_2/3docs` multi-doc 子集（HitAll@K / doc_recall）  
- [x] confidence 去硬编码（`grounding.estimate_answer_confidence`）  
- [x] LightRAG score 暴露 `synthetic` 标记  
- [x] Skill 结果回灌 Answer  
- [x] 面试演示脚本 [`09-interview-demo-script.md`](09-interview-demo-script.md)  

### 依赖图

```
Orchestrator 统一 ──┬── Tool loop ──┬── skill.run tool
                    │               └── mcp.call 真/mock
                    ├── Skill handlers 证据化
                    ├── Verifier / Compliance 合并
                    └── EvalRunner 同路径
                              ▲
CRUD import ── id 对齐 ── Hit@K 看板
                              └── RAGAS optional
```

**关键路径**：无 Tool loop → Skill/MCP 仍是侧车 HTTP；无 id 对齐 → Hit@K 空数；无统一编排 → eval 与产品两套故事。

---

## 8. 成功标准（验收清单）

- [x] 线上问答与 eval 走同一 orchestrator  
- [x] 一次真实对话能产生 **真实** `ToolCallLog`（非 suggested）  
- [x] 至少一个 Skill 在无证据时拒绝编造  
- [x] MCP：≥1 非 mock server `tools/list` + `call` 成功；mock 响应含 `status=mock`  
- [x] 导入 CRUD Demo-S 后，Eval 页显示 **非零、可解释** 的 MRR / Hit@5（依赖本地语料与索引）  
- [x] RAGAS 关闭不装成功；开启失败时 reason / metrics_source 可见  
- [x] README / 架构表述与代码一致，无「多智能体平台」过度承诺  
- [x] Eval Run 支持 JSON/CSV 导出（`GET /api/eval/runs/{id}/export`）  

---

## 9. 面试叙事（对齐实现）

1. **问题**：企业政策问答要可审计、可拒答、可评测，不能只做 ChatGPT 套壳。  
2. **架构**：RAG + 多层记忆 + tool-using 主 agent；Skill = 规程；MCP = 外部协议。  
3. **不是假 multi-agent**：Router / Answer / Verifier 有分工；检索是服务。  
4. **评测**：CRUD-RAG 风格金标；主指标 Hit@K/MRR；页面可导入可对比；RAGAS 可选。  
5. **诚实边界**：企业 SaaS mock 有标注；demo 用采样集；全量 8 万可扩展但非默认。  

### 可能追问

| 问 | 答 |
|---|---|
| 是 multi-agent 吗？ | Supervisor 流水线 + tool-using 主 agent，不是群聊框架 |
| Skill vs Tool？ | Tool 原子可审计；Skill 业务规程，可调 LLM/Tool |
| MCP 为何不全真？ | 协议层真；缺租户密钥的连接器用 mock adapter，日志可区分 |
| Hit@K 怎么算？ | gold doc id ∩ top-k；MRR=1/first_hit_rank；见 `retrieval_metrics.py` |
| 为何不用满 8 万？ | 索引与 LLM 成本；采样可复现；架构支持全量 |

---

## 10. 落地状态（实现时维护）

| 项 | 状态 | 更新日期 | 备注 |
|---|---|---|---|
| 策略文档 | 已写入 | 2026-07-14 | 本文 |
| Orchestrator 统一 | **已完成** | 2026-07-14 | chat SSE 调 `pipeline.run`；去掉假 skill.suggest diagnostics |
| Tool loop | **已完成（基础）** | 2026-07-14 | `complete_with_tools` + AnswerAgent loop + ChatToolExecutor；依赖 provider 支持 tools |
| Skill 真执行 | **已完成（基础）** | 2026-07-14 | 证据绑定 handler + enable_skill 时 registry 真跑；可经 `skill.run` tool |
| MCP 真 client | **已完成（基础）** | 2026-07-14 | `MCPClient` stdio/http + demo server；mock 仍保留且标注 status=mock |
| CRUD 导入 | **已完成（基础）** | 2026-07-14 | `POST /api/eval/datasets/crud-import` + external_id + 前端导入卡 |
| Eval 看板增强 | **已完成（基础）** | 2026-07-14 | MRR/Hit@K 卡片 + `compare_strategies` → strategy_comparison 表 |
| RAGAS 真跑 | **已完成（可选）** | 2026-07-14 | 有 ragas 依赖则真跑；否则 token-overlap proxy 并标注 metrics_source；默认关 |
| Verifier | **已完成（加强）** | 2026-07-14 | 拒答一致性、悬挂引用、引用-证据错配、可疑无依据数值 |
| Router LLM 结构化 | **已完成** | 2026-07-14 | need_skill / tool_hints / rewrite_query 已输出并接线 pipeline |
| Hard refuse 无证据 | **已完成** | 2026-07-14 | `CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=true` 默认 |
| Router 字段驱动下游 | **已完成** | 2026-07-14 | rewrite 改 retrieval query；need_skill 控制 skill 执行 |
| Skill 结果回灌 Answer | **已完成** | 2026-07-14 | Skill 先于 Answer 执行，结构化 output 进入最终回答 prompt |
| Multi-doc HitAll@K | **已完成** | 2026-07-14 | `hit_all_at_k` / `doc_recall_at_k`；CRUD 2/3docs 导入校验 gold 文档数 |
| LightRAG synthetic score | **已完成** | 2026-07-14 | `metadata.score_is_synthetic=true` + `score_method=rank_decay` |
| confidence 去硬编码 | **已完成** | 2026-07-14 | `agents/grounding.py`；pipeline 用 verifier warnings 重算 |
| claim–evidence 词重叠门 | **已完成** | 2026-07-14 | `WEAK_CLAIM_EVIDENCE_SUPPORT`；非 LLM judge |
| Eval 导出 JSON/CSV | **已完成** | 2026-07-14 | `/api/eval/runs/{id}/export` + 评估页按钮 |
| 面试演示脚本 | **已完成** | 2026-07-14 | `docs/09-interview-demo-script.md` |
| 本地 Rerank | **已完成（诚实）** | 2026-07-14 | `RerankService`=lexical fusion；`metadata.rerank_method=local_lexical_fusion`；非 cross-encoder |
| Router tool_hints 过滤工具表 | **已完成** | 2026-07-14 | `resolve_allowed_tools` + pipeline `ToolAllowlist` diagnostics |
| 评估专用「测试库」隔离 | **已完成** | 2026-07-14 | code=`eval_test`；CRUD 导入默认进测试库，避免污染业务 KB |
| 渐进式多步规划 L1 | **已完成** | 2026-07-20 | Router `complexity/plan_steps` + `plan_normalize` + SSE plan/plan_step + 思考中 checklist；非开放 Planner Agent |
| 渐进式多步规划 L2 | **已完成** | 2026-07-20 | `PlanExecutor` 真按步执行；`depends_on`/waves；独立 retrieve 等 `asyncio.gather` 并行；`CHAT_PLAN_EXECUTOR`/`CHAT_PLAN_PARALLEL` |
| 渐进式规划 L2.5 ToT 选路 | **已完成** | 2026-07-20 | `difficulty/reasoning_mode` 三档；`plan_branch` 候选；双请求 HITL；eval 自动 recommended；前端选路 UI |
| 正式 TurnState + 错误集中写入 | **已完成** | 2026-07-20 | `TurnState`/`TurnError` 单轮黑板；L1/L2 失败写入 `errors[]`；`PipelineResult.errors` + diagnostics；非 peer 消息、非分布式状态机 |
| LLM Reflection 闭环 | **已完成（基础）** | 2026-07-20 | `CritiqueAgent`+`ImproveAgent` 双 prompt；`ReflectionLoop` 硬顶 2 轮；6 维 + PASS 出口；仅高风险触发；Eval 默认关；hard refuse 永不反思；非 peer 群聊 |
| 负样本评测集 | **已完成** | 2026-08-28 | 企业套件 40 条（20 off_topic + 20 near_miss）；标记在 `relevance_judgement.negative`；不进 Hit@K/MRR，单独算 `negative_gate` |
| 跑题门控分数化 | **已完成（诚实）** | 2026-08-28 | cross-encoder 分数优先、词面覆盖率兜底；阈值 `-8.0` 为保守默认，见 §12 标定流程 |
| 编排跑在真图上（Option A） | **已完成（诚实）** | 2026-09-22 | `AgentPipeline.run` 体内驱动真 `StateGraph`（`backend/app/graph/pipeline_graph.py`：`route→tot?→execute`），节点体从旧 `_run_impl` 逐字迁移；Chat/Eval 同经此图；`PipelineResult` 形状与 SSE 顺序不变，全套契约绿。**边界**：与 `builder.py` durable 证据路径图**并存的第二个图**（共享 fail-closed 证据门语义，非同一节点词表；本图不 checkpoint，ToT 靠 service 层双请求恢复） |
| 存储无关 run parity（PG） | **已完成（诚实）** | 2026-09-22 | `scripts/graph_pg_parity.py` → `GraphService.run` 在 aiosqlite 与活库 PostgreSQL(55432) 得同一 evidence_gate/终态/有序 RunEvent（`artifacts/graph/stage3/pg_parity.json`，PARITY_OK）。**边界**：确定性依赖替身检索，**非**生产语料答案 parity；生产 Chat/Eval 端点仍走 `AgentPipeline`（现为图驱动，未切到 `GraphService`），详见 `specs/001-enterprise-agent-refactor/tasks.md` Phase 3 落地状态 |
| 生产路由经 adapter（默认打开·可回退） | **已完成（诚实，切换本体上线）** | 2026-09-23 | `ROUTE_VIA_GRAPH_ADAPTER` **默认改为 `True`**：`routes_chat`/`routes_eval` **现默认**经 `backend/app/graph/route_adapter.py::GraphRouteAdapter` 记录 removal-ledger 遥测（`route_chat`/`route_chat_stream`/`route_eval`）并委派共享 pipeline 图；置 `False` 回滚到字节等价旧直连路径（保留至 Stage 9 零使用窗口清零）。默认打开下 f4/f5/f6+route-adapter-live+entrypoint-conclusion-parity（30）+ broad sweep 无回归（4 环境性失败与本改无关，已在干净基线复现）。**边界**：底层是 pipeline 图（Option A），**非** durable `GraphService`；Eval 入口真实语料结论 parity 需运行栈 + 真实语料，本机缺检索栈**未验证**（见下行、`scripts/graph_realcorpus_parity.py`）。adapter 纯透传（仅加遥测、同一 pipeline 图），确定性替身层已证 flag-on==flag-off 逐字相等 |
| 生产入口结论 parity（chat/stream） | **部分（诚实）** | 2026-09-23 | `tests/integration/test_entrypoint_conclusion_parity.py` → 同库运行时切 flag，有证据/无证据两问下 `/api/chat` 与 `/api/chat/stream` 结论字段 flag-on==flag-off 且 chat==stream 逐字相等（`artifacts/graph/stage3/entrypoint_conclusion_parity.json`，PARITY_OK）。**边界**：确定性替身检索（F4LightRAG/F4LLM），**非**真实语料答案 parity；Eval 真实语料 parity 仍需运行栈 |
| 真实语料 parity（词面臂 + embedding 臂均已跑通） | **两臂均已完成（诚实）** | 2026-09-24 | `scripts/graph_realcorpus_parity.py` 两臂均在**真实 CRUD 语料**（`questanswer_1doc`）上证得 `ROUTE_VIA_GRAPH_ADAPTER=False vs True` 逐例结论（有序 doc id + Hit@K/MRR + passed + score）**逐字相等**：<br>① **词面臂**（`bm25_only`，BM25 直读 `content_text`，不需 provider）：50 例 + 200 干扰 → `artifacts/graph/stage3/realcorpus_parity_bm25.json`（`PARITY_OK`，`route_eval` 遥测=1）。<br>② **embedding/hybrid 臂**（`hybrid_lightrag_bm25`，默认策略）：真实双 provider——chat=SenseNova `sensenova-6.8-flash-lite`（LightRAG 索引期抽取实体/关系）+ embedding=NVIDIA `nvidia/llama-nemotron-embed-vl-1b-v2`（dim=2048）。5 doc 经 in-process LightRAG **真实建图索引**（`indexed=5`，非空索引硬门控通过）后跑检索 parity → `artifacts/graph/stage3/realcorpus_parity.json`（`PARITY_OK`，3 例 off==on，`route_eval` 遥测=1）。harness 用 `EVAL_CHAT_*`/`EVAL_EMBED_*` 六环境变量注**密文密钥**（代码无明文）、维度自探测、索引完成硬门控（`indexed==0` 拒绝空索引，绝不伪造绿）。**边界**：① embedding 臂 N 小（3 例 + 2 干扰）——因 `sensenova-6.8-flash-lite` 是推理模型、抽取延迟高（偶发 ReadTimeout，已把 provider 超时提到 300s），大 N 索引耗时且撞配额；parity 是**恒等性质**（同一索引上 off==on），小 N 已足证切换透明，词面臂 N=50 覆盖同一 adapter 路径。② 1-doc 整篇匹配 MRR/Hit 天然偏高（embedding 臂 hit@1=1.0），此处只主张 parity 不主张检索质量。不比生成答案文本（LLM 采样与 flag 独立） |
| 检索证据门合流到规范路径 | **已完成（诚实）** | 2026-09-23 | T048：新建 `backend/app/retrieval/evidence_gate.py`——**物理迁移**当前检索质量门（`assess_retrieval_quality`/`off_topic_reason`/`resolve_gate_thresholds`/`QualityDecision`，含 `insufficient_evidence` 语义）并**再导出**确定性候选门 `evaluate_evidence_gate`（实现仍在 `graph/evidence_gate.py`，不 fork）→ 单一导入面。生产 importer（`pipeline`/`plan_executor`/`eval_runner`）已切到规范路径，旧 `rag/quality_gate.py` 降为薄再导出。**诚实命名**：cross-encoder 分数门仅在真配时生效，词面覆盖率是兜底，绝不标为 cross-encoder。`test_guardrails`+`test_evidence_gate_parity`（25）绿 |
| T053 删除第二套 stage（Stage 9） | **有意推迟（观察窗口未清零）** | 2026-09-23 | 切换默认现已打开（开启 removal-ledger 观察窗口）；删除旧 `AgentPipeline` 第二套 stage 须先由 `AdapterUsageTelemetry.zero_use_over_window()` 在**真实部署观察窗口**内证 legacy 回滚分支（`ROUTE_VIA_GRAPH_ADAPTER=False`）零使用，且需先在 durable `GraphService` 节点重建富 `ChatResponse` 输出面（~1200 行）并经真实语料验证。窗口本会话无法流逝。故留作 Stage 9（T159）动作，非遗漏 |
| Phase 4 US2 并发/持久（Stage 4） | **部分（诚实，非 AI 面，详见 tasks.md）** | 2026-09-24 | durable job 状态机 + 事务性 outbox（version CAS，SQLite 全绿 **且真实 PG 恰好一次并发领取已验证 R16**）、Redis token bucket + lease semaphore、Redis Streams 有界 replay/resume/gap→snapshot、有界 SSE 通道背压/心跳、outbox relay（MockTransport 标 `status=mock`）、Celery quorum 拓扑（config introspection 无 broker）——均在**可用基础设施**上真跑通。**R16 起 PostgreSQL 17.10 于 55432 真实起立**：PG 集成+安全 39 passed / 0 skip（含 `FOR UPDATE SKIP LOCKED` 交错、multi-instance、tenant isolation、migrations），Stage-4 契约 61 + recovery 10 全绿。**R17 起 Stage-4 六张表已有 Alembic 迁移** `003_stage4_concurrency.py`（expand 相位，自应用 forced RLS+`tenant_isolation`+`policyflow_app` grant；`test_stage4_migration.py` 5 passed 真 PG，`test_postgres_migrations.py`+`test_tenant_isolation.py` 16 passed 不回归）——生产 `alembic upgrade head` 现真会建表，v1.21 迁移缺口闭合。**R18 起 T066 的 PG 权威写回补齐**：新增 `backend/app/quota/ledger.py`（`QuotaLedger`），在真实 PG（55432）实现 policy 就近解析（tenant+workload→tenant→global，取最高 active version，为 Redis 令牌桶/租期信号量播种）、`quota_leases` 租期审计（开/关一次性幂等，重复 release 不改写终态）、`usage_records` 只追加写回（reserved/actual 分列 + PG 侧聚合）——`test_quota_ledger_pg.py` 8 passed 真 PG（与 Redis coordinator 合跑 15 passed），coordinator/ledger 保持原子决策 vs 持久权威的可组合分离；请求路径接线归 T069/T070。**R19 起 T067 的 SSE 恢复 PG 权威快照接线补齐**：`RunEvent`（唯一 `(run_id, sequence)`）+ `RunEventRepository` + graph 执行器 append 本已在，缺的是把持久里程碑服务进 SSE 恢复——新增 `backend/app/sse/snapshot.py`（`DurableRunSnapshot`）按 sequence 有序读回并接进 `sse_event_source`：遇 gap 发 `snapshot_required` 后回放 PG 权威快照（纯 PG，扛住 Redis 全量 flush），`GET /api/v2/runs/{run_id}/events` 已注入（`test_run_snapshot_pg.py` 5 passed 真 PG + 生成器组合 2 tests，`test_sse_resume`/`test_graph_run_persistence`/`test_runs_api` 28 passed 不回归）；快照后实时尾续订仍依赖 T064。**仍 gated**：本机 RabbitMQ 关闭 → 实况 publish/consume/redeliver(T062/T064/T065 broker 部分)、Locust 1000-SSE/饱和(T072) 未验证。**R20 起 T069 的 `cancel` 端点补齐（broker-free）**：`POST /api/v2/runs/{run_id}/cancel` 经 `JobService.request_cancel` 做 version-CAS→`cancel_requested`+`job.cancel_requested` outbox（同事务恰好一次），租户域授权复用 `get_run`（未知/跨租户 404 先于 transition），幂等，终态 409 `RUN_NOT_CANCELLABLE`（`test_runs_api.py` 15 passed，+5 cancel）；协作取消而非硬杀，worker 停止循环仍 gated 于 T064/T072。`/api/v2/runs` 的 `POST /runs`+`GET /runs/{run_id}`+`cancel`(T069) 与 `GET /runs/{run_id}/events`(T068，打真实 Redis 回放/追赶) 已全绿；实时尾 producer、生产 Redis 配额绑定仍 gated。**R21 起 T071 的 graph-node 遥测 call-site 已接**：`build_pipeline_graph` 用 `_instrument_node` 包裹 route/tot/execute，记节点时延/失败（进程内自洽，`test_pipeline_graph_telemetry.py` 2 tests，`test_pipeline_tot`+`test_reflection_loop` 18 passed 不回归）；**诚实缺口**：queue-depth/lease gauge 因跨实例 delta 不自洽故不接，待 T072 用 observable-gauge 查 DB COUNT 或单进程约束落地。**R22 起 T070 的 document-index call-site 已迁 DurableJob/outbox（broker-free）**：新增 `backend/app/jobs/runner.py`（`submit_document_index` + `LocalJobRunner` + `JobHandlerRegistry`/`JobContext`），`routes_kb` 上传/重索引（×2）与 `routes_faq` 审核通过（×1）三处长时 `BackgroundTasks(process_document_index)` 换成「先 `await enqueue(kind=document_index, idempotency_key=<RagIndexJob id>)` 落持久性/幂等/`job.enqueued` outbox，再把单进程 `LocalJobRunner.drain_once`（与 Celery 消费者同一 version-CAS lease→start→complete/fail 状态机）挂为唯一 drain nudge」；durable 行只存 JSON id，live 依赖运行时经 `JobContext` 解析（`test_durable_job_runner.py` 5 passed + `test_document_index_durable_submission.py` 2 passed + `test_phase1_knowledge` 等合跑 46 passed 不回归）；**仍剩**：routes_eval 三处 + 生产 broker 消费者（T064）gated 于 RabbitMQ（T072）。**R23 起 T070 的 routes_eval 三处 call-site 已迁 DurableJob/outbox（broker-free），T070 应用层 call-site 迁移完成（生产 broker 消费者仍 gated）**：`post_eval_run` 的长时 eval 执行换成「先 `await enqueue(kind=eval_run, idempotency_key=<EvalRun id>)`、payload 只放 JSON（`run_id`/`tenant_id`/`EvalRunCreate.model_dump(json)`），再挂 `drain_once` drain nudge」——`ROUTE_VIA_GRAPH_ADAPTER` 分支移进 `_handle_eval_run`，live RAG service/pipeline/route adapter 运行时经 `JobContext.app_state` 解析不序列化；`post_crud_import`/`post_enterprise_eval_seed` 的 document 索引改走新 `submit_document_index_batch`（按 pending `RagIndexJob` id 批量入列后**单次** drain nudge，不再每文档一个 BackgroundTask），只读 `pending_index_job_id` 查幂等键而不 claim（claim 仍属 `process_document_index`）。TDD 红→绿：`tests/contract/test_eval_run_durable_submission.py` 1 passed（创建 run 产出恰一个 `eval_run` DurableJob + 一个 `job.enqueued` outbox、idempotency_key==run_id、state succeeded）+ `test_durable_job_runner.py` 新增 `eval_run` kind 注册断言（6 passed）；广回归 `test_phase4_faq_eval`+`test_phase5_acceptance`+`test_enterprise_seed_negatives`+`test_f5_frontend_contract`+`test_document_index_durable_submission` 合跑全绿，ruff clean。**honest 边界**：`execute_eval_run`/`adapter.run_eval` 同样自吞域异常，故 eval_run durable job succeeded == 「eval 尝试跑完」而非「评测必成」，域级失败仍落 `EvalRun` 行（旧语义），durable 重试不会因域失败触发。**R24 起 T069 的生产 admission 绑定补齐（broker-free，闭合 v1.25/allow-all 缺口）**：新增 `backend/app/quota/admission.py`（`CoordinatorAdmission` 实现 `RunAdmission` Protocol + `QuotaLimits`），把 `/api/v2/runs` 的准入由默认 allow-all 绑到真实 Redis `QuotaCoordinator`（T066 令牌桶+租期信号量，Lua 原子、跨实例一致）：`admit` 把 `QuotaDecision` 映射为 `AdmissionOutcome`（429 `RATE_LIMITED` / 503 `CONCURRENCY_SATURATED` + 数值 `Retry-After`），经 `app.state.run_admission` 注入。**honest 边界**：准入放行时**持有**并发租期而不在此 release（run-terminal 的显式归还属 worker，gated 于 T064），到期由 `lease_ms` TTL 回收（保守：绝不泄漏槽位，短 run 可能过度预留，正是 coordinator 为之而建的 crash-safe 回收模式）；per-(resource,identity) 限额由 `limits_provider` 提供，生产 provider 走 `QuotaLedger.resolve_policy`（PG 权威）在 PG 可达时另接，故适配器仅依赖 Redis 即可测。TDD 红→绿：新增 `tests/contract/test_coordinator_admission.py` **4 passed** 真 Redis（不可达即 skip）：`POST /api/v2/runs` 预算内→201 queued、令牌桶耗尽→429+Retry-After（`RATE_LIMITED`）、并发租期饱和→503+Retry-After（`CONCURRENCY_SATURATED`），+ 适配器级断言「放行后租期仍被持有（`active_slots==1`）→二次 admit 饱和映射 503」；回归 `test_runs_api`+`test_quota_admission`+`test_durable_job_runner` 合跑 **28 passed**，ruff clean。**仍剩（未伪造）**：生产 broker 消费者（T064）gated 于 RabbitMQ（T072）；本机 RabbitMQ 仍关 → 实况 publish/consume/redeliver、worker 侧租期显式归还、Locust 1000-SSE/饱和（T072）、Independent Test 未验证。**Checkpoint 未宣布达成**。**R25 起 T071 的 queue-depth/lease 诚实缺口（R21 记录）已闭合（broker-free）**：新增 `backend/app/jobs/metrics.py`（`job_state_counts(engine)` 同步跑 `SELECT state, priority_lane, COUNT(*) GROUP BY ...` + `install_job_state_gauge`）与 telemetry 的 `register_job_state_gauge`/observable gauge `policyflow.jobs.state`——回调在采集时查权威 DB 计数，报**绝对值**而非进程内 delta，故跨实例/重启一致；`main.py` lifespan 在 `configure_telemetry`+建表后对同步 engine 接线。TDD 红→绿：`tests/contract/test_job_state_gauge.py` 4 passed（按 `(state,lane)` 等于 DB 计数、改库后二次采集报新绝对值证明 pull-based、`reset_telemetry` 后清空），`test_stage4_telemetry`/`test_pipeline_graph_telemetry`/`test_durable_job_runner`/`test_runs_api` 合跑不回归，ruff clean。**边界**：进程内 `record_job_queue_depth`/`record_job_lease` delta 计数保留作每进程速率信号，权威深度以本 gauge 为准；实时尾 producer（T064）、Locust（T072）、Independent Test 仍 gated。**LLM concurrency/tokens call-site 已接（R26，broker-free）**：在 `OpenAICompatibleLLMService._post_json`（所有 LLM HTTP 调用唯一 choke point）接线——进入请求 `record_llm_concurrency(delta=1)`、`finally` 恒 `delta=-1`（报错/超时也归还 gauge，无泄漏），成功响应后 `_record_usage_tokens` 从 `usage` 块按方向计 prompt/completion（兼容 Responses API 的 input/output_tokens），无 usage 不计、不伪造 0、telemetry 异常被吞不污染调用路径；`tests/contract/test_llm_telemetry.py` 4 passed，`test_llm_rate_limit`/`test_phase2_rag_chat`/167 遥测相关不回归，ruff clean。**仍未接**：通用 cleanup pass call-site（SSE teardown 已 R15 计入）。**T072 broker-free + 真 PG 子集证据已落盘（R27，T072 仍标 `[ ]`）**：本机真 PostgreSQL 17.10（55432）+ 真 Redis（6379）上跑三组 Stage-4 套件并把真实输出存入 `artifacts/recovery/stage4/`（`pytest-pg-stage4.txt`+`infra-probe.txt`+`README.md`）：integration 28 + contract 99 + recovery 12 = **139 passed 真 PG+Redis**（recovery 含重复投递幂等 ×5 + 重启恢复/reap ×5 + reap 遥测 ×2）。**R28 起 generic cleanup call-site 闭合**：`JobService.reap_expired_leases`（lease 过期恢复扫除，回收死/断连 worker 的 durable-job 槽位与并发租期）用 `finally` 恒记 `cleanup.duration` scope=`job_lease_reap`（空扫也记，不伪造缺失），`tests/recovery/test_reap_telemetry.py` 2 passed——至此 T071 instrument call-site 全部接齐（SSE/graph-node/job-state gauge/LLM concurrency·tokens/两处 cleanup）。**仍 `[ ]`**：recovery 以直驱状态机**模拟**重投/worker 死亡，非活 broker 往返；RabbitMQ 本机确实 DOWN（5672/15672 超时），Locust 1000-SSE/饱和、活 broker `kill -9` 重投、Independent Test 未跑，绝不 mock broker 冒绿，**Checkpoint 仍未宣布达成**。完整逐任务边界见 `specs/001-enterprise-agent-refactor/tasks.md` Phase 4 落地状态（R16） |

---

## 11. 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-07-14 | 合并去玩具化、多智能体落点、Skill/Tool/MCP 诚实实现、CRUD Eval 面试页策略；基于代码审阅写入 |
| v1.1 | 2026-07-14 | Phase 0–3 主线/加分落地：synthetic score、grounding confidence、claim 门、导出、面试脚本；勾选 §7/§8 |
| v1.2 | 2026-07-14 | 本地 lexical rerank 可用；§1 增加改造后快照，避免基线表被误读为当前状态 |
| v1.3 | 2026-07-20 | L1 渐进式规划：Router 结构化计划字段、plan_normalize、Pipeline 驱动与前端步骤清单 |
| v1.4 | 2026-07-20 | L2 PlanExecutor：依赖波次、独立子任务并行、证据 bag、与 L1 共存开关 |
| v1.5 | 2026-07-20 | L2.5 ToT 选路：difficulty 三档、候选计划、双请求 HITL、eval auto-pick、前端选路；诚实非学术 ToT |
| v1.6 | 2026-07-20 | 正式 `TurnState` 共享记录 + `errors[]` 集中写入；PlanExecutor/Pipeline/L1 接线；diagnostics 透出；面试文档诚实边界 |
| v1.7 | 2026-07-20 | Critique→Improve 反思闭环：双 prompt、6 维 + PASS、硬 max_rounds=2、高风险触发、Eval 默认关；Compliance 仍为规则门 |
| v1.8 | 2026-08-28 | 负样本评测集（40 条）+ 跑题门控从纯词面覆盖率升级为「cross-encoder 分数优先、词面兜底」；新增 §12 |
| v1.9 | 2026-09-22 | 编排层忠实移植到真 LangGraph（Option A）：`AgentPipeline.run` 体内驱动 `StateGraph`，Chat/Eval 同图；`GraphService.run` 在活库 PostgreSQL 验证存储无关 run parity。诚实边界：与 durable 证据路径图并存的第二个图；确定性替身检索非生产语料答案 parity（详见 `specs/001-enterprise-agent-refactor/tasks.md`）|
| v1.10 | 2026-09-23 | 生产 Chat/Eval 路由经 `GraphRouteAdapter`，behind 可回退 flag `ROUTE_VIA_GRAPH_ADAPTER`（默认关）：开时记录 removal-ledger 遥测并委派共享 pipeline 图，关时字节等价旧路径。诚实边界：底层是 pipeline 图非 durable `GraphService`；真实语料结论 parity 因本机缺检索栈未验证 |
| v1.11 | 2026-09-23 | 新增真实语料 parity 可运行入口 `scripts/graph_realcorpus_parity.py`（硬门控 `PROVIDER_NOT_CONFIGURED`，待用户补齐 OpenAI 兼容凭据即可跑真栈确定性检索 parity）；T053 经用户裁定「保持门控」列为 Stage 9 有意推迟。二者写入 §10 状态表，Checkpoint 仍不宣布达成 |
| v1.12 | 2026-09-23 | T048 完成：检索证据门合流到规范 `backend/app/retrieval/evidence_gate.py`（迁移质量门 + 再导出确定性候选门；旧 `rag/quality_gate.py` 薄再导出）。T051/T052 切换本体上线：`ROUTE_VIA_GRAPH_ADAPTER` **默认改 `True`**，生产 Chat/stream/Eval 默认经 `GraphRouteAdapter`（legacy 保留为回滚分支）。全套契约在默认打开下无回归（4 环境性失败已在干净基线复现，与本改无关）。**残留（硬阻塞，非跳过）**：真实语料 Eval-arm parity（待凭据）+ T053 删除（Stage 9 观察窗口）；Checkpoint 仍不宣布达成 |
| v1.13 | 2026-09-24 | 真实语料 parity **词面臂跑通**：`scripts/graph_realcorpus_parity.py` 参数化出 `bm25_only` 臂（BM25 直读 `content_text`，不需 embedding provider）。真实 CRUD 50 例 + 200 干扰，`ROUTE_VIA_GRAPH_ADAPTER` OFF vs ON 逐例结论逐字相等 → `artifacts/graph/stage3/realcorpus_parity_bm25.json`（`PARITY_OK`）。T054 → `[X]`。**残留（未伪造）**：embedding/hybrid 臂因用户端点 `token.sensenova.cn/v1` 仅 chat 无 embedding 模型未跑，harness 对该臂仍硬门控 `PROVIDER_NOT_CONFIGURED`；T053 Stage 9 删除未动。Checkpoint 仍由用户裁定，不单方宣布达成 |
| v1.14 | 2026-09-24 | embedding 臂 harness 升级为**分离双 provider**（`EVAL_CHAT_*`/`EVAL_EMBED_*` 六环境变量注密文密钥、维度自探测、索引完成硬门控），并**实测了用户新补的 embedding 端点**。结果（诚实，未产出绿）：embedding 端点（NVIDIA `nvidia/llama-nemotron-embed-vl-1b-v2` dim=2048）连通 200 ✓；但 chat 端点（SenseNova `deepseek-v4-flash`）**两处受限**——配额耗尽（429 `insufficient_quota`/`tpm/rpm limit`）+ 推理模型抽取 prompt 下 `content` 空（正文进 `reasoning_content`）→ LightRAG 逐 chunk `LLM response content is empty`，doc 全 `failed`，harness **正确拒绝空索引 parity**（`no documents indexed successfully; refusing hollow parity`）。安装缺失依赖 `lightrag-hku==1.5.4`。结论：embedding 臂 parity 仍未产出，属外部 chat 端点能力/配额限制而非代码缺陷；换一个能承载抽取调用量且返回非空 `content` 的 chat 端点即可补跑。词面臂绿仍是真实语料级切换透明性证据。T053 Stage 9 未动，Checkpoint 不单方宣布 |
| v1.15 | 2026-09-24 | **embedding/hybrid 臂 parity 现已跑通（真实绿）**：换用 chat=SenseNova `sensenova-6.8-flash-lite`（同为推理模型但答案落在 `content`，非 `reasoning_content`，且无配额阻塞）替下不可用的 `deepseek-v4-flash`；provider 超时 120→300s（推理模型抽取慢，偶发 ReadTimeout）、索引等待 900→1800s。5 doc 经 in-process LightRAG **真实建图索引**（`indexed=5`）后，`hybrid_lightrag_bm25` 策略下 `ROUTE_VIA_GRAPH_ADAPTER` OFF vs ON 逐例结论逐字相等（3 例 off==on）→ `artifacts/graph/stage3/realcorpus_parity.json`（`PARITY_OK`，`route_eval` 遥测=1）。至此**两臂（词面 + embedding）均在真实语料证得切换透明**。**边界（诚实）**：embedding 臂 N 小（3 例 + 2 干扰），因推理模型抽取慢 + 撞配额，大 N 索引不经济；parity 是恒等性质（同一真实索引上 off==on），小 N 已足证，词面臂 N=50 覆盖同一 adapter 路径。1-doc 整篇匹配 hit@1=1.0 是天然虚高，此处只主张 parity 不主张检索质量。residual ① 至此清零；T053 Stage 9 删除仍未动（观察窗口本会话无法走完），Checkpoint 仍不单方宣布达成 |
| v1.16 | 2026-09-24 | **Phase 3 (US3) Checkpoint 经用户裁定达成 ✅**：在完整知悉诚实边界后，用户裁定 Checkpoint 达成——三层 parity 证据（确定性替身 + 词面臂真实语料 N=50 + embedding 臂真实语料 N=3）、durable PG 重启恢复（T050）、无未批准副作用、所有入口默认经共享 route adapter（`ROUTE_VIA_GRAPH_ADAPTER=True`）全部满足。**唯一 carve-out**：T053（删除 `AgentPipeline` 第二套 stage 执行）划归 **Stage 9（T159）单独跟踪**，仍 `[~]`——需真实部署零使用观察窗口 + durable graph 富输出面重建，非 Phase 3 门槛判据。**诚实红线保留**：底层执行仍是 pipeline 图（Option A）非 durable `GraphService`；embedding 臂小 N smoke；1-doc 天然虚高只主张 parity。**T053 未伪造为 `[X]`**——Checkpoint 达成 ≠ 旧 stage 已删除 |
| v1.17 | 2026-09-24 | **Phase 4 (US2) 并发/持久推进（部分，非 AI 面）**：在可用基础设施上真跑通并提交——DurableJob 状态机 + 事务性 outbox（version CAS，SQLite）、Redis token bucket + lease semaphore、Redis Streams 有界 replay/resume/gap→snapshot、有界 SSE 通道背压/心跳/cleanup、outbox relay（`OutboxPublisher`，`MockTransport` 标 `status=mock`）、Celery quorum 拓扑（`build_celery_app`，config introspection 无 broker：quorum/publisher confirms/late-ack+reject_on_worker_lost/prefetch=1/DLQ/软硬 timeout）。合计 ~40 tests GREEN（真实 Redis 8.2.0 / SQLite / asyncio）。**gated（诚实，未伪造绿）**：本机 RabbitMQ(5672)/PostgreSQL(55432) 均关闭 → 实况 publish/consume/redeliver、`FOR UPDATE SKIP LOCKED` 权威路径、配额 PG 写回、`/api/v2/runs`(T069)、遥测(T071)、Locust 1000-SSE/饱和(T072) 未验证；T064 consumer 装配未建。逐任务边界见 `specs/001-enterprise-agent-refactor/tasks.md` Phase 4 落地状态（R13）。**Checkpoint 未宣布达成** |
| v1.29 | 2026-10-01 | **Phase 4 T069 生产 admission 绑定补齐（R24，broker-free，闭合 v1.25 的 allow-all 缺口）**：`/api/v2/runs` 的准入自 v1.18 起只是可注入的 `RunAdmission`、默认 `_AllowAllAdmission`，生产的 Redis 绑定一直悬空（v1.25 记为「默认 allow-all，实况 429/503 归 T072」）。本轮新增 `backend/app/quota/admission.py`：`CoordinatorAdmission` 实现该 Protocol，把准入绑到真实 Redis `QuotaCoordinator`（T066 令牌桶 + 租期信号量，Lua 单线程原子、跨实例一致）——`admit` 把 `QuotaDecision` 映射为 `AdmissionOutcome`（放行→admitted；`rate_limited`→429 `RATE_LIMITED`、`concurrency_saturated`→503 `CONCURRENCY_SATURATED`，均带数值 `Retry-After`），经 `app.state.run_admission` 注入，与 allow-all 默认并存（Redis 可达才绑）。限额经 `QuotaLimits` + `limits_provider`（sync/async 皆可）注入，生产 provider 走 `QuotaLedger.resolve_policy`（PG 权威，`QuotaPolicy` 的 tokens_per_window/window_seconds/max_concurrency 播种）在 PG 可达时另接，适配器自身只依赖 Redis。TDD 红→绿：新增 `tests/contract/test_coordinator_admission.py` **4 passed** 真 Redis（打 127.0.0.1:6379，不可达即 skip、唯一前缀、teardown 清键）：端到端 `POST /api/v2/runs` 预算内→201 queued、令牌桶耗尽→429+Retry-After、并发租期饱和→503+Retry-After，外加适配器级 `active_slots==1` 断言放行后租期仍被持有→二次 admit 映射 503；回归 `test_runs_api`(15)+`test_quota_admission`(含真 Redis)+`test_durable_job_runner` 合跑 **28 passed**，ruff clean。**honest 边界**：准入放行时**持有**并发租期而不在此 release——run-terminal 的显式归还属 worker 循环（T064，broker-gated），落地前由 `lease_ms` TTL 回收（保守：绝不泄漏槽位，短 run 可能过度预留，即 coordinator 既定的 crash-safe 模式；租期到期本身不构成重复副作用的许可，恰好一次仍归 durable outbox）。**仍 gated（未伪造）**：生产 broker 消费者（T064）、worker 侧租期显式归还、实时尾 producer、Locust 1000-SSE/饱和（T072）、Independent Test，RabbitMQ 仍关；**Checkpoint 仍未宣布达成** |
| v1.33 | 2026-10-01 | **Phase 4 T071 最后一个 generic cleanup call-site 闭合（R28，broker-free，闭合 v1.31/R26 遗留缺口）**：R26 诚实记录「通用 cleanup pass call-site 仍未接（SSE teardown 已 v1.15/R15 计入，其余未接）」。本轮把 Phase 4 真正拥有的另一处资源清理 pass——lease 过期恢复扫除 `JobService.reap_expired_leases`（回收死/断连 worker 占住的 durable-job 槽位与并发租期，正是 Stage-4「断连资源 30s 内被释放」要观测的恢复通道）——接上 `cleanup.duration`：`perf_counter` 自计时、`finally` 恒 `record_cleanup_duration(scope="job_lease_reap", ...)`，扫除抛错或 reap 空列表也记一次 timed 观测（扫到 0 的恢复 pass 仍是真 pass，不伪造缺失）。TDD 红→绿：新增 `tests/recovery/test_reap_telemetry.py` **2 passed**（in-memory meter：reap 命中记 scope=`job_lease_reap` count=1 且 sum≥0、无过期租约也记 count=1）；回归 `tests/recovery/`（12 passed，含 reap 遥测 2）+ `test_stage4_telemetry`/`test_job_state_gauge`/`test_durable_job_runner`/`test_runs_api`/`test_sse_endpoint`/`test_job_state_machine`（70 合跑）全绿；`backend/app/jobs/service.py` 预存 N818（`JobIdempotencyConflict` 命名）为既有项、非本轮引入未触碰。**PG 证据刷新**：`artifacts/recovery/stage4/pytest-pg-stage4.txt` 重跑把 recovery 由 10→12（整体 **139 passed 真 PG+Redis**）。至此 **T071 的 instrument call-site 全部接齐**（SSE/graph-node/job-state observable gauge/LLM concurrency·tokens/两处 cleanup——SSE teardown + job lease reap）；T071 仍标 `[~]` 仅因 gauge/counter 导出需 collector/OTLP endpoint 且与 broker 侧 worker 循环（T064）相邻。**仍 gated**：T062/T064/T065 活 broker、T072 Locust/`kill -9` 重投、Independent Test，RabbitMQ 本机仍关；**Checkpoint 仍未宣布达成**。 |
| v1.32 | 2026-10-01 | **Phase 4 T072 broker-free + 真 PG 子集证据落盘（R27，T072 仍标 `[ ]`、Checkpoint 不宣布）**：T072 要求把终态一致性与资源清理证据存到 `artifacts/recovery/stage4/`。本机确起真 PostgreSQL 17.10（127.0.0.1:55432，DB `policyflow`/`policyflow_test`）+ 真 Redis（6379），故把**不依赖 broker**的那部分证据诚实跑出并落盘：`infra-probe.txt`（PG UP / Redis `+PONG` / RabbitMQ 5672·15672 均 TimeoutError DOWN）、`pytest-pg-stage4.txt`（`POLICYFLOW_TEST_DATABASE_URL` 指 55432：integration 28 passed〔PG 权威 lease 并发/配额账本/run 快照/Stage-4 迁移/多实例/index claim〕+ contract 99 passed〔job 状态机/outbox publisher/runs API/SSE resume·endpoint·cleanup/quota·coordinator admission/job-state gauge/LLM telemetry/durable runner/celery config/stage4 telemetry/document·eval durable submission〕+ recovery 10 passed〔重复投递幂等 ×5：同 payload 重投 no-op、完成不二次 transition、终态后重投不复活、cancel-finalize no-op、重复 outbox 发布被 DB 拒；重启恢复 ×5：死 worker 租约回收重派、运行中 job 重启被 reap、attempt 预算耗尽终态、succeeded 不被扫、sweep 幂等〕= **137 passed 真 PG+Redis**）、`README.md`（分列「已覆盖」vs「仍 gated」）。**仍 `[ ]` 的原因（诚实）**：recovery 套件以直驱状态机**模拟**重投与 worker 死亡（DB/状态机层），**非**活 broker 消费者往返；Locust 1000-SSE/saturation profile、活 broker `kill -9` 重投、Independent Test 全部**未跑**，T062/T064/T065 broker 依赖项仍 gated。绝不 mock broker 冒绿；T072 保持 `[ ]`，**Checkpoint 仍未宣布达成**。 |
| v1.31 | 2026-10-01 | **Phase 4 T071 LLM concurrency/tokens call-site 补齐（R26，broker-free，闭合 v1.26/v1.30 遗留的 LLM 遥测无 call-site 缺口）**：`record_llm_concurrency`/`record_llm_tokens` instrument 自 v1.18/R14 已建但从无接线（gauge 不动、token 不计）。本轮在 `OpenAICompatibleLLMService._post_json`——`complete`/`complete_with_tools` 的 chat.completions 与 responses 分支都经此发请求的唯一 HTTP choke point——接线：进入请求即把 in-flight gauge +1，内层 `try/finally` 保证 -1 恒执行（provider 报错/超时/重试耗尽时先归还 gauge 再映射 ApplicationError，无泄漏）；成功解析响应后 `_record_usage_tokens(data)` 从 `usage` 块取 prompt/completion（兼容 Responses API 的 input/output_tokens），按 `direction` 计入 `policyflow.llm.tokens`，无 `usage` 不计（不伪造 0）、负值/非 int 归 0、记录异常被 `try/except` 吞——observability 绝不中断 LLM 调用路径。TDD 红→绿：`tests/contract/test_llm_telemetry.py` 4 passed（in-memory meter + MockTransport：in-flight 读 1、完成/500 报错后归 0、按方向计 12/7、无 usage 不计）；回归 `test_llm_rate_limit`+`test_stage4_telemetry`+`test_job_state_gauge`+`test_error_audit_contract`(167)+`test_phase2_rag_chat`(5) 全绿，ruff clean。**仍 gated**：通用 cleanup pass call-site（SSE teardown 已 v1.15/R15 计入 `cleanup.duration`，其余未接）、T064 实时尾 producer、T072 Locust/`artifacts/recovery/stage4/`、Independent Test，本机 RabbitMQ 仍关；**Checkpoint 仍未宣布达成**。 |
| v1.30 | 2026-10-01 | **Phase 4 T071 queue-depth/lease 权威 gauge 补齐（R25，broker-free，闭合 R21/v1.26 诚实缺口）**：v1.26（R21）明确记录 queue-depth/lease **不**用进程内 delta up/down counter 接——一个 job 的 enqueue 与 lease 常落在不同实例，跨实例 delta 永不自洽——并把「正确做法是 observable gauge 回调查 DB `COUNT(*)`」归为 T072/collector 待办。本轮直接落地该正确做法：telemetry 新增 `METRIC_JOB_STATE = "policyflow.jobs.state"` + `register_job_state_gauge(provider)`（在当前 meter 上创建**一次** OTel observable gauge，稳定回调 `_job_state_callback` 在每次采集时读 `_STATE.job_state_provider`；`_NullMeter.create_observable_gauge` 保证 OTel 缺失时 no-op，`reset_telemetry`/meter 变更清 provider+handle，故 reset 后回调返空而非报陈旧值）；新增 `backend/app/jobs/metrics.py`（`job_state_counts(engine)` 跑同步 `SELECT state, priority_lane, COUNT(*) GROUP BY state, priority_lane`，`install_job_state_gauge(engine)` 把该查询注册为 provider）；`main.py` lifespan 在 `configure_telemetry` + `initialize_database`（建表）后对同步 `engine` 接线。**为何正确**：gauge 是 pull-based，报数据库权威**绝对值**而非累加 delta，故每个实例读同一深度、重启不丢不重复计。TDD 红→绿：新增 `tests/contract/test_job_state_gauge.py` **4 passed**（in-memory meter：采集点按 `(state, lane)` 等于 DB 分组计数、改库后二次采集报新绝对值证明 pull-based/跨实例语义、`job_state_counts` 直接匹配 GROUP BY、`reset_telemetry` 后 gauge 清空）；回归 `test_stage4_telemetry`(8)+`test_pipeline_graph_telemetry`(2)+`test_durable_job_runner`(6)+`test_runs_api`(15) 合跑全绿，ruff clean。**边界**：进程内 `record_job_queue_depth`/`record_job_lease` delta 计数**保留**作每进程速率信号，权威队列深度以本 observable gauge 为准（二者并存、不互相覆盖）；gauge 导出需配 collector/OTLP endpoint，无 endpoint 时在 in-memory reader 下可验证。**仍 gated（未伪造）**：LLM concurrency/tokens 与 generic cleanup call-site、实时尾 producer（T064）、worker 侧租期显式归还、Locust 1000-SSE/饱和（T072）、Independent Test，RabbitMQ 仍关；**Checkpoint 仍未宣布达成** |
| v1.18 | 2026-09-24 | **Phase 4 T069 `/api/v2/runs` 落地（R14）**：`backend/app/api/routes_runs.py` 提供 `POST /runs`（DurableJob 幂等 enqueue）+ `GET /runs/{run_id}`；`tests/contract/test_runs_api.py` 10 tests GREEN（SQLite，PG-/broker-free）：Idempotency-Key 16–128 长度门（越界 400）、同 key 同 payload 幂等返回同 run、同 key 异 payload 409、跨租户读 404、过载经可注入 `RunAdmission` 返回 429/503 + 数值 `Retry-After`（直返 `JSONResponse`，因 error handler 不透传 header）。**gated**：`events`(SSE HTTP 端点，T068 余项)/`cancel` 端点未建；生产 admission 绑定 Redis 配额协调器(T066)未接线（默认 allow-all），实况配额决策归 T072。**Checkpoint 仍未宣布达成** |
| v1.19 | 2026-09-24 | **Phase 4 T071 Stage-4 遥测 instrument（R14）**：`backend/app/observability/telemetry.py` 增 8 个 OTel instrument + 记录 helper——active SSE 连接、按 lane 的 queue depth、held lease、LLM concurrency、LLM tokens（prompt/completion）、graph node latency（histogram）+ failure、cleanup duration。经 in-memory meter 验证（`tests/contract/test_stage4_telemetry.py`，8 tests GREEN）；标签策略仍拒 identifier 形状值（如 UUID node 名）。**gated**：instrument 已定义可导出，但尚未接入实时调用点（SSE channel/JobService lease·enqueue/graph 执行器/cleanup pass）——call-site 装配为余项。**Checkpoint 仍未宣布达成** |
| v1.20 | 2026-09-24 | **Phase 4 T068 SSE HTTP 端点（R15）**：`backend/app/sse/endpoint.py` 的 `sse_event_source` 组合 RunEventStream 回放（resume 点被 trim → 先发 `snapshot_required` 控制帧，令客户端回 PostgreSQL 快照）与 BoundedEventChannel 实时尾（heartbeat 透传、channel 关闭即净收释放连接）；`GET /api/v2/runs/{run_id}/events` 租户域授权（未知/跨租户 run 在开流前即 404），经 `_instrumented_stream` 在连接开/合移动 active-SSE gauge 并把 teardown 计入 `sse` cleanup histogram（T071 首个实时 call-site 接线）。`tests/contract/test_sse_endpoint.py` 10 tests GREEN：5 生成器级单测（无依赖）+ 5 HTTP 端点测（打真实 Redis，unreachable 即 skip；回放为 SSE 帧 / Last-Event-ID resume / 跨租户 404 / 未知 run 404 / 遥测经 in-memory meter 验证 gauge+cleanup）。**gated**：实时尾 producer（worker 事件扇入每连接 channel）依赖 Celery 消费者(T064)，端点当前只跑回放/追赶模式；T071 其余 call-site（JobService/graph/cleanup）未接；1,000 SSE/饱和/Locust 归 T072。**Checkpoint 仍未宣布达成** |
| v1.21 | 2026-09-24 | **Phase 4 真实 PostgreSQL 起立 + T063 权威 claim 验证（R16）**：从既有 `.pgdata` 启动 PostgreSQL 17.10 于 127.0.0.1:55432（本地 `trust` 认证）。先前因 PG 关闭而 skip 的套件现**全部真跑通、零 skip**：PG 集成+安全 **39 passed**（migrations / multi_instance / index_claim 真实 `FOR UPDATE SKIP LOCKED` 交错 / graph_pg_checkpoint / graph_restart / v2_dependencies / tenant_isolation）、Stage-4 契约 **61 passed**（含真实 Redis quota/SSE）、recovery **10 passed**。新增 `tests/integration/test_job_lease_pg_concurrency.py`：30 jobs / 8 并发 worker 在真实 PG（READ COMMITTED）下 version-CAS **恰好一次领取**（`attempts==1`、`version==2`、无重复 transition），把 T063「权威 claim 未在真实 PG 跑」转为真绿。**诚实新缺口**：Stage-4 六张表（durable_jobs/outbox_events/quota_policies/quota_leases/usage_records/capacity_test_runs）**无 Alembic 迁移**，仅 `metadata.create_all` 建；生产 `alembic upgrade head` 不会建表——需补迁移（T072 前置/独立任务）。**仍 gated（未伪造）**：RabbitMQ 仍关（T064 consumer、T062/T065 真实往返、T072 broker 部分）；1,000 并发实时 SSE 依赖 T064 producer（现有 Locust `sse` profile 打 Stage-1 `/api/chat/stream`）；T070 需 executor 设计决策。**Checkpoint 仍未宣布达成** |
| v1.22 | 2026-09-24 | **Phase 4 Stage-4 Alembic 迁移补齐（R17，闭合 v1.21 缺口）**：新增 `migrations/versions/003_stage4_concurrency.py`（`revision='003'`, `down_revision='002'`, expand 相位）——纯增量创建 Stage-4 六张表（durable_jobs/outbox_events/quota_policies/quota_leases/usage_records/capacity_test_runs）。因 002 enforce 早于本表存在，003 **自应用**同款保障：五张 tenant 表 `ENABLE`+`FORCE ROW LEVEL SECURITY` + `tenant_isolation` 策略（迁移角色是 owner，不 FORCE 则策略被静默绕过），并把 `policyflow_app` 的 DML 重新 grant 到新表；`quota_policies.tenant_id` 保留可空 + null-tolerant 策略（全局策略共享），`capacity_test_runs` 无 tenant_id 故无 RLS。TDD 红→绿：新增 `tests/integration/test_stage4_migration.py`（**5 passed** on 真实 PG，`upgrade head` 达 003 建全六表 / 五张表 forced RLS+策略 / capacity 表无 RLS / durable_jobs 在 `policyflow_app` 下真跨租户隔离含 WITH CHECK 拒写 / 全局 quota_policies 行对每租户可见）；`test_postgres_migrations.py`+`test_tenant_isolation.py` **16 passed** 不回归（enforce fixtures 固定到 "002")。SQLite 便携性 sanity：`upgrade head`→建六表(version 003)、`downgrade 002`→全部 drop（RLS/grant 为 PG-guarded）。生产 `alembic upgrade head` 现真会建 Stage-4 表——v1.21 诚实缺口闭合。**仍 gated（未伪造）**：RabbitMQ 仍关、1,000-SSE/饱和/Locust 归 T072；**Checkpoint 仍未宣布达成** |
| v1.23 | 2026-09-24 | **Phase 4 T066 PG 权威写回补齐（R18，闭合 R13 缺口）**：新增 `backend/app/quota/ledger.py`（`QuotaLedger`）——把 R13 起悬空的「policy/final usage/audit 写回 PostgreSQL」在真实 PG（55432）落地，与 `coordinator.py` 的 Redis 原子决策保持可组合分离（原子决策 vs 持久权威，比照 `JobService` 与 coordinator）。① **policy 权威**：`resolve_policy(tenant_id, workload)` 按 tenant+workload→tenant→global+workload→global 四层就近解析，命中层内取最高 `active` version，为 Redis 令牌桶/租期信号量播种（限额存 DB 不写死调用点）。② **租期审计**：`record_lease` 以 Redis lease id 为 `owner` 写 `quota_leases` 开放行，`close_lease` 一次性幂等盖章 `released_at`+`outcome`（首关生效，重复 release/reaper 竞争不改写终态，与 outbox 恰好一次同构）。③ **usage 写回**：`record_usage` 只追加 `usage_records`（reserved 于准入 / actual 于完成分列，绝不改写），`usage_totals` 在 PG 侧聚合分开报。TDD 红→绿：新增 `tests/integration/test_quota_ledger_pg.py`（**8 passed** 真 PG，PG 关停即 skip），与 Redis coordinator 合跑 **15 passed**。**请求路径接线**（admit 由 `resolve_policy` 播种、命中即写 lease/usage）归 T069/T070；**仍 gated（未伪造）**：RabbitMQ 仍关、1,000-SSE/饱和/Locust 归 T072；**Checkpoint 仍未宣布达成** |
| v1.26 | 2026-09-30 | **Phase 4 T071 graph-node 遥测 call-site 接线 + queue-depth 诚实缺口（R21，broker-free）**：R15 起 T071 只接了 SSE gauge 一个 call-site。本轮接进程内自洽的 graph-node 时延/失败 call-site：`backend/app/graph/pipeline_graph.py` 新增 `_instrument_node(name, fn)`，`build_pipeline_graph` 用它包裹 `route`/`tot`/`execute` 三节点——每次遍历记 `graph.node.duration` histogram，抛异常时 `graph.node.failures` counter +1 后原样重抛，节点名为稳定有界标签（非 identifier），未配 meter 时 no-op，不改返回值/异常/控制流。**为何 histogram 在此正确**：graph 节点在同一进程 start→finish，进程内聚合自洽。**诚实缺口（未伪造）**：JobService 的 queue-depth/lease **不**用 delta up/down counter 接——一个 job 的 enqueue 与 lease 常在不同实例，跨实例 delta 不自洽；正确做法是 observable gauge 回调查 DB `COUNT(*) WHERE state IN (...)` 或明确单进程约束，归 T072/collector；本轮只记录该发现。TDD 红→绿：`tests/contract/test_pipeline_graph_telemetry.py` 2 tests 经 in-memory meter（最小 fake pipeline 驱动编译图，无 agents/LLM/infra）——成功遍历只计 route/execute（未走 tot 无点无 failure）、抛错节点计 failure 且仍计时延；`test_pipeline_tot`+`test_reflection_loop` **18 passed** 不回归、`test_stage4_telemetry` 8 passed，ruff clean。**仍 gated（未伪造）**：LLM concurrency/tokens、cleanup pass、queue/lease gauge 待接；RabbitMQ 关；1,000-SSE/饱和/Locust 归 T072；**Checkpoint 仍未宣布达成** |
| v1.28 | 2026-10-01 | **Phase 4 T070 routes_eval call-site 迁 DurableJob/outbox（R23，broker-free；应用层 call-site 迁移完成）**：承接 R22（document-index 三处），本轮迁 `routes_eval` 剩余三处长时 `BackgroundTasks`——至此 T070 的**应用层 call-site 全部迁完**（生产 broker 消费者仍 gated）。① **eval-run**：`runner.py` 新增 `EVAL_RUN_KIND` + `_handle_eval_run`（`ctx.app_state is None` 即诚实 no-op，否则运行时从 `app_state` 解析 live RAG service/pipeline/settings/route adapter，`ROUTE_VIA_GRAPH_ADAPTER` 分支由路由移入 handler）+ `submit_eval_run`（`await enqueue(kind=eval_run, idempotency_key=<EvalRun id>)`，payload 只放 JSON：`run_id`/`tenant_id`/`EvalRunCreate.model_dump(json)`，再挂单次 `drain_once` nudge）；`default_registry` 注册 `eval_run`。`post_eval_run` 去掉内联 `pipeline` 本地与 if/else `add_task`。② **document-index 批量**：`runner.py` 新增 `submit_document_index_batch(items=[(document_id, idempotency_key), ...])`——批量 `enqueue(kind=document_index)` 后**单次** drain nudge（不再每文档一个 BackgroundTask）；`indexing_service.py` 新增只读 `pending_index_job_id`（查最新 pending `RagIndexJob` id 作幂等键而**不** claim，claim 仍属 `process_document_index`）；`post_crud_import`/`post_enterprise_eval_seed` 按 `result.pending_index_document_ids` → `pending_index_job_id` 组 items 后批量提交。TDD 红→绿：`tests/contract/test_eval_run_durable_submission.py` **1 passed**（创建 run 产出恰一个 `eval_run` DurableJob + 一个 `job.enqueued` outbox、idempotency_key==run_id、payload.run_id==run_id、state succeeded）+ `test_durable_job_runner.py` 新增 `eval_run` kind 注册断言（**6 passed**）；广回归 `test_phase4_faq_eval`（POST→pending、GET→success 端到端）+ `test_phase5_acceptance`+`test_enterprise_seed_negatives`+`test_f5_frontend_contract`+`test_document_index_durable_submission` 合跑全绿（12+10 passed），ruff clean。**honest 边界**：`execute_eval_run`/`adapter.run_eval` 同样自吞域异常，故 eval_run durable job succeeded == 「eval 尝试跑完」而非「评测必成」，域级失败仍落 `EvalRun` 行（旧语义），durable 重试不因域失败触发（`max_attempts` 对这两类 handler 实质无效）。**仍剩（未伪造）**：生产 broker 消费者（T064）gated 于 RabbitMQ（T072），T070 行仍标 `[~]`；**Checkpoint 仍未宣布达成** |
| v1.27 | 2026-10-01 | **Phase 4 T070 document-index call-site 迁 DurableJob/outbox（R22，broker-free）**：把长时 `BackgroundTasks(process_document_index)` 换成持久提交。本轮先迁 **document-index** 三处 call-site（`routes_kb` 上传 + 显式重索引 ×2、`routes_faq` 审核通过 ×1，共享一个 handler），eval-run 面留待。新增 `backend/app/jobs/runner.py`：`DOCUMENT_INDEX_KIND` + `JobContext`（运行时承载 sync engine/LightRAG adapter/app.state，durable 行只存 JSON id 不序列化活对象）+ `JobHandlerRegistry` + `_handle_document_index`（无 adapter 即诚实 no-op，否则 `await process_document_index`）+ `LocalJobRunner.drain_once`（循环 `lease`→`start`→handler→`complete`/`fail`，与未来 Celery 消费者 T064 **同一** version-CAS 状态机，`max_jobs` 防自旋）+ `submit_document_index`（先 `await enqueue(kind=document_index, idempotency_key=<RagIndexJob id>)` 把持久性/幂等/`job.enqueued` outbox 在响应前落 DB，再把 `drain_once` 挂为唯一 BackgroundTask drain nudge，请求仍非阻塞）。幂等键取 `RagIndexJob` id：同一索引尝试重投递去重，新上传/新重索引各自入列。TDD 红→绿：`tests/contract/test_durable_job_runner.py` **5 passed**（lease/run/complete + result_ref + job.enqueued/job.succeeded；空 drain=0；handler 异常在零 backoff 默认下走满 attempt 预算到 terminal_failed、先 job.recoverable_failed 后一次 job.terminal_failed；未知 kind→`NO_HANDLER` terminal_failed；default_registry 含 document_index）+ `tests/contract/test_document_index_durable_submission.py` **2 passed**（上传产出恰一个 document_index DurableJob + 一个 job.enqueued outbox、idempotency_key==index_job_id、state succeeded；两次重索引产出两个幂等键互异的 durable job）；广回归 `test_phase1_knowledge`（上传→`indexed` 端到端）+ `test_phase4_faq_eval`+`test_phase5_acceptance`+`test_runs_api`+`test_job_state_machine` 合跑 **46 passed**，ruff clean。**honest 边界**：`process_document_index` 自吞域异常，故 durable job succeeded == 「handler 跑完」而非「索引必成」，域级失败仍落 document 行（旧语义）；LocalJobRunner 是显式单进程 dev/single-node drain，生产换 broker 消费者打同一 durable 行，不伪造。**仍剩（未伪造）**：`routes_eval` 三处 BackgroundTasks（crud-import/enterprise-seed 可复用 document_index kind；eval-run 需新 `eval_run` kind + handler）未迁；生产 broker 消费者（T064）gated 于 RabbitMQ（T072）；**Checkpoint 仍未宣布达成** |
| v1.25 | 2026-09-30 | **Phase 4 T069 run 取消端点补齐（R20，broker-free）**：`JobService.request_cancel/finalize_cancel` 的协作取消状态机（version-CAS + `job.cancel_requested`/`job.cancelled` outbox，恰好一次）本已存在，缺的只是 HTTP 表面。新增 `POST /api/v2/runs/{run_id}/cancel`：经 `request_cancel` 做 version-CAS → `cancel_requested` 并同事务写 outbox；租户域授权完全复用 `get_run`（未知/跨租户 run 在任何 transition 前即 404，取消不能探测他租户 run）；协作取消而非硬杀（被领取 worker 在步骤间观察标志后停止，该循环仍 gated 于 broker 消费者 T064/T072）；重复请求幂等（仍 200/`cancel_requested`），终态（succeeded/failed/cancelled）经 `JobStateError`→409 `RUN_NOT_CANCELLABLE`。TDD 红→绿：`tests/contract/test_runs_api.py` 新增 5 cancel 测（协作取消 / 幂等 / 跨租户 404 / 未知 404 / 终态二次取消 409）SQLite broker-free 全绿 **15 passed**（原 10+5），ruff clean。**仍 gated（未伪造）**：生产 admission 绑定 Redis 配额协调器（T066）未接线（默认 allow-all，实况 429/503 归 T072）；RabbitMQ 仍关；worker 协作停止依赖 T064/T072；**Checkpoint 仍未宣布达成** |
| v1.24 | 2026-09-24 | **Phase 4 T067 SSE 恢复的 PostgreSQL 权威快照接线（R19，闭合 R13「待补 DB 侧持久 milestone」缺口）**：诚实定位——`RunEvent` 模型（`UniqueConstraint("run_id","sequence")`）、`RunEventRepository`（append/next_sequence/list_for_run）与 graph 执行按阶段追加 milestone **早已存在**（迁移 001）；真正缺口是 `snapshot_required` 控制帧承诺「回 PostgreSQL 取权威快照」，却**没有任何服务端面把持久 milestone 喂进 SSE 恢复**——milestone 被搁置，Redis 被 flush/trim 后恢复无源。新增 `backend/app/sse/snapshot.py`（`DurableRunSnapshot`）：每次读开一个短的租户域 UoW（`set_tenant_context` + `run_events.list_for_run(after_sequence=, limit=)`），按 sequence 有序回放，纯 PG（构造上survive 全量 Redis flush），milestone→`StreamEvent`（`id=d{sequence}`，与 Redis `ms-seq` id 区分）。`sse_event_source` 新增可选 `snapshot` 参数：gap 时先发 `snapshot_required`，若 wired 则回放 PG 权威 milestone 且**不**再双发部分 Redis 尾（快照是超集权威），未 wired 则回退保留的 Redis 事件（既有生成器测试不变，向后兼容）。`GET /api/v2/runs/{run_id}/events` 经 `_durable_snapshot(request).bind(tenant_id, run_id)` 接线（`app.state.run_snapshot` 可注入覆盖）。TDD 红→绿：`ModuleNotFoundError` 确认红→实现绿；新增 `tests/integration/test_run_snapshot_pg.py`（**5 passed** 真 PG，PG 关停即 skip：乱序插入按 sequence 有序 + payload 保真 + id `["d1","d2","d3"]` / after_sequence resume / 跨租户空 / Redis 无内容仍可读 / bind 产出零参 reader）+ `test_sse_endpoint.py` 生成器组合 **2 tests**（gap+durable 快照回放 PG milestone 且部分 Redis `run.progress` 不双发 / 无快照回退保留 Redis）。回归：SSE contract + PG snapshot **17 passed**、`test_sse_resume`+`test_graph_run_persistence`+`test_runs_api` 全绿，ruff clean。**仍 gated（未伪造）**：快照后的实时尾续传依赖 T064 producer / broker；RabbitMQ 仍关；1,000-SSE/饱和/Locust 归 T072；**Checkpoint 仍未宣布达成** |

### Rerank implementation update (2026-08-02)

The default remains `local_lexical_fusion`. An optional NVIDIA Cross-Encoder backend
is now wired through the existing `Reranker` Protocol. It uses a three-model
round-robin/fallback chain and records `rerank_provider` plus `rerank_model` in the
Evidence metadata. It fails explicitly when the full chain is unavailable.

---

## 12. 负样本评测集 + 跑题门控（2026-08-28）

### 12.1 要解决什么

「跑题门控」是回答前的最后一道闸：检索确实返回了片段，但片段跟问题没关系时，
应该丢掉证据、按「无可靠证据」拒答，而不是拿着不相关的制度条款硬答。

原来这道闸只看**词面覆盖率**：把问题切成中文二元词（「差旅住宿」→「差旅」「旅住」「住宿」），
数一下有多少个出现在召回片段里，覆盖率低于 `0.08` 就判跑题。问题有两个：

- **漏拦**：同一套企业话术的问题很容易「借词」。问「婚假几天」，库里没有婚假制度，
  但《员工手册》里有「假」「员工」「申请」这些词，覆盖率一算就过关了。
- **误拦**：换个说法就崩。问「住宿报销额度」，制度写的是「旅馆费用上限」，
  语义完全对上，词面一个都不重合，反而被拦掉。

### 12.2 没有负样本就标不出阈值

想把闸门换成「重排分数低于 X 就判跑题」，得先知道 X 取多少。
`scripts/analyze_rerank_scores.py` 就是干这个的：从已跑过的检索评测里把分数捞出来，
按「是不是金标」分两堆，扫一遍阈值，看每个阈值误拒多少、拦住多少。

第一次跑完发现一个硬问题：**评测集里全是「该答上」的题**。
230 条 top-1 全都命中了金标，「该被拦掉」的那一侧一个样本都没有。
只有下界（阈值不能高过多少，否则误杀正样本），没有上界（阈值要多高才拦得住跑题）。
所以先补负样本。

### 12.3 负样本套件：40 条 = 20 off_topic + 20 near_miss

写在 `backend/app/services/enterprise_eval_dataset.py` 的 `POLICY_NEGATIVES`，
跟着企业评测套件（12 篇政策 / 200 条正样本）一起 seed。两类：

| 类型 | 数量 | 长什么样 | 为什么要它 |
|---|---|---|---|
| `off_topic` | 20 | 「明天北京天气」「快排怎么写」「火锅底料配方」 | 完全不沾企业语境，任何闸门都该拦住——这是**底线**，拦不住说明门控坏了 |
| `near_miss` | 20 | 「婚假能休几天」「期权行权价怎么定」「手机通讯费能报吗」 | 一样的企业话术、一样的提问方式，但**语料里根本没有这个主题** |

`near_miss` 是真正的难点，也是这次改造的靶子。这 20 条的主题都用 grep 在 12 篇政策里
逐个确认过出现 0 次，所以「拒答」是唯一正确答案。有两条是刻意设的陷阱：

- 「手机通讯费」——语料里出现过一次「即时通讯工具」，词面能借到「通讯」；
- 「离职提前多久通知」——「离职」在语料里出现 5 次（在别的语境里），
  所以它算 near_miss 而不是 off_topic。

负样本没有金标，标记放在 `RetrievalEvalItem.relevance_judgement` 这个自由 JSON 字段里
（`negative: true` + `negative_kind`），不用改表、不用迁移。判定逻辑集中在
`backend/app/evals/negatives.py`，避免各处自己 `judgement.get("negative")`。

### 12.4 指标怎么算才不虚高

**负样本绝对不能进 Hit@K / MRR。** 它们没有金标，进去就是白送 0 分，
把检索指标做低；反过来如果按「命中即算对」也是白送满分。所以分成两条路：

- 正样本：Hit@1/5/10、MRR、`hit_all_at_k`（原样不动），
  额外记 `gate_false_block`（新门控误拦了吗）和 `lexical_false_block`（老门控会误拦吗）。
- 负样本：只算 `gate_blocked`（拦住了吗），额外记 `lexical_blocked`（老门控拦得住吗），
  聚合到 `metrics["negative_gate"]`，并按 `by_kind` 拆开 off_topic / near_miss。

这样**一次 run 同时给出四个数**：新门控的误拦率、新门控的拦截率、
老门控的误拦率、老门控的拦截率。改进是量出来的，不是嘴上说的。

`scope.label` 里的 `N=` 只数正样本，负样本单独写 `neg=`，
简历上的 `Hit@1 = x%（N=100）` 不会被 40 条负样本悄悄摊薄。

另外补了一处数据安全：`cleanup_eval_dataset()` 原来会删掉「没有金标」的条目
（当成金标文档已被删除的脏数据），负样本正好符合这个特征。现在
`_is_stale_retrieval_item` 先判负样本直接放过，否则 seed 完一次卫生清理就没了。

### 12.5 门控实现：分数优先、词面兜底

改造后的门控在 `backend/app/rag/quality_gate.py`，三个新配置项：

| 配置 | 默认 | 含义 |
|---|---|---|
| `RETRIEVAL_GATE_CROSS_ENCODER_ENABLED` | `true` | 有真 cross-encoder 分数时是否用分数判 |
| `RETRIEVAL_GATE_MIN_CROSS_ENCODER_SCORE` | `-8.0` | 分数阈值（**logit，不是 0-1 相似度**） |
| `RETRIEVAL_GATE_MIN_OVERLAP_RATIO` | `0.08` | 兜底的词面覆盖率阈值（沿用原值） |

关键的诚实点：**默认聊天路径根本没有 cross-encoder 分数**。
`ChatRequest.rerank_enabled` 默认 `false`，聊天也从不传 `reranker_method`，
所以如果只写「分数低于 X 判跑题」，这行代码在聊天里永远不执行，就是摆设。
因此门控按分数的**来源**分流：

- `metadata.rerank_method == "cross_encoder"` → 用阈值判，`gate="cross_encoder_score"`，
  被拦时 reason code 是 `RETRIEVAL_SCORE_BELOW_THRESHOLD`；
- 其余情况（重排关闭 / `local_lexical_fusion` / RRF 合成分）→ 退回词面覆盖率，
  `gate="lexical_overlap"`，行为与改造前完全一致。

`local_lexical_fusion` 也不参与打分判定，因为它本身就是词面信号，
用它去判词面相关性等于自己给自己背书，没有增量。RRF 的 `score = 1/(60+rank)`
更是只反映排名（`metadata.score_is_synthetic=true`），跟语义无关。

两个信号**始终都算、都上报**（`gate` / `top_score` / `score_threshold` /
`lexical_supported` / `overlap_ratio`），这才是 §12.4 那四个数的来源。

顺手清了一处重复：`pipeline.py` 和 `plan_executor.py` 原来各自再调一次
`question_evidence_support`，现在统一消费 `assess_retrieval_quality` 返回的
`quality["off_topic"]`，门控行为只有一个地方能改。

### 12.6 阈值 −8.0 是怎么来的，边界在哪

现有的 230 条金标 top-1 分数里最低 −12.52，取 −8.0 在这批数据上误拒率 0%。
**这是保守下界，不是标定完成的最优值**——上界还没量出来，
因为量上界需要一次「开 cross-encoder 重排 + 选进 40 条负样本」的 run，
而 NVIDIA key 只以密文存在 `model_providers.api_key_ciphertext` 里。

标定流程（拿到 key 之后照着走）：

1. 评估中心 seed 企业评测套件（会一起写入 40 条负样本）；
2. 建 run：`rerank_enabled=true` + `reranker_method=cross_encoder`，
   正样本随机 50–100 条，**外加全部 40 条负样本**；
3. `python scripts/analyze_rerank_scores.py --threshold -8`
   —— 表格左边是误拒率（代价），右边是负样本拦截率并按 off_topic / near_miss 拆开（收益）；
4. 按「误拒率 ≤2%」挑最高的阈值（脚本的 `建议：` 一行直接给），
   回填 `RETRIEVAL_GATE_MIN_CROSS_ENCODER_SCORE`；
5. 复跑一次，确认正样本 Hit@K 没掉、`negative_gate.gate_blocked` 上去了。

面试时的诚实表述：
「跑题门控我做了两件事——先补了 40 条负样本（20 条完全跑题 + 20 条同话术但库里没有），
再把闸门从纯中文二元词覆盖率改成 cross-encoder 分数优先、词面兜底。
两个信号我都记，所以一次 run 能同时给出新旧门控的误拦率和拦截率。
阈值现在是 −8.0，来自 230 条金标 top-1 分数的下界（误拒 0%），
是个保守值；上界还没标完，需要一次开 cross-encoder 的 run，
我把标定脚本和流程都写进文档了。默认聊天是不开重排的，
所以分数门在聊天里不生效，走的还是词面兜底——这点我不含糊。」

