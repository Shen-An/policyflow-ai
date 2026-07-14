# 08. 去玩具化 / 多智能体 / Skill·Tool·MCP / Eval 改造总策略

版本：v1.1  
日期：2026-07-14  
状态：**Phase 0–3 主线与加分项已基本落地**（见 §10）  
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
                    │  Verifier   │  规则 + 可选 LLM  ← 质量门 Agent
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
| Verifier | 规则/LLM | 否 | Agent | 几乎无 → citation/拒答门 |
| Memory | 部分 | 否 | Service/后台 | 已较强，保留 |
| Compliance 关键词 | — | — | 删除或合并 | 并入 Verifier |

### 3.3 面试安全表述

> 不是 CrewAI 式多角色群聊。是 **Supervisor 式流水线**：Router 结构化路由，Answer 做 tool-using 主 agent，Skill/Verifier 为可选专职节点；检索与记忆是服务。可观测、可评测、可审计。

### 3.4 多智能体「加在哪」（清单）

1. **Router** — 关键词 → LLM 结构化（domain, risk, task_type, need_skill, tool_hints, rewrite）  
2. **Answer tool loop** — 最大收益的 agent 化  
3. **SkillExecutor** — 真执行 registry  
4. **Verifier** — citation / 无证据拒绝 / 合并 compliance  
5. **（可选）QueryRewrite** — 并入 Router 或独立一小步  
6. **不要**并行六个 agent 互聊  

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
  → Verifier (citations / refuse / warnings)
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

---

## 11. 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-07-14 | 合并去玩具化、多智能体落点、Skill/Tool/MCP 诚实实现、CRUD Eval 面试页策略；基于代码审阅写入 |
| v1.1 | 2026-07-14 | Phase 0–3 主线/加分落地：synthetic score、grounding confidence、claim 门、导出、面试脚本；勾选 §7/§8 |
| v1.2 | 2026-07-14 | 本地 lexical rerank 可用；§1 增加改造后快照，避免基线表被误读为当前状态 |
