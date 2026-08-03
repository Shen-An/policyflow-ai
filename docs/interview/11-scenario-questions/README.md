# 11. 场景面试题（10 道 + 参考解答）

版本：v1.0  
日期：2026-07-22  
用途：按**真实业务场景**演练口述；每题对齐代码/文档边界，可指路径，不背简历注水话术。

> 配套：  
> - [面试知识库总目录](../README.md)  
> - [诚实边界](../09-honesty-boundaries/README.md)  
> - [演示与高频 Q&A](../10-demo-qa/README.md)  
> - 策略总文档：[`../../08-de-toy-multiagent-skill-eval-strategy.md`](../../08-de-toy-multiagent-skill-eval-strategy.md)

**使用建议**

| 时长 | 用法 |
|---|---|
| 开场前 30 min | 只过题干 + 一句话结论 + 必说边界 |
| 模拟面试 | 对方读场景，你 2–4 分钟答；再补代码落点 |
| 深挖 | 每题「追问」小节；答不出就回诚实边界清单 |

**原则**：先说**怎么做**，再说**为什么不做某类假方案**，最后点**指标/路径/边界**。

---

## 题目一览

| # | 场景主题 | 考察点 |
|---|---|---|
| 1 | 领导要「多智能体平台」一页 PPT | 架构诚实性 / AgentPipeline |
| 2 | 胡话问题也「像模像样」答了 | 证据绑定 / hard refuse |
| 3 | 简历写 Hybrid 显著优于 BM25 | 检索策略 + Hit@K/MRR 解读 |
| 4 | Skill / Tool / MCP 分不清 | 分层定义与可审计性 |
| 5 | 用户只说「给我模板」 | 多轮 + query rewrite |
| 6 | 记忆把过期制度当真理 | 四层记忆非权威 |
| 7 | 评测 100% Hit@1 被质疑刷分 | eval_test / 干扰 / 采样 N |
| 8 | 「你们做了 cross-encoder 重排？」 | 本地 lexical fusion 诚实表述 |
| 9 | 前端一直转圈 / 不知道在干什么 | SSE 阶段与信息层级 |
| 10 | 高风险回答仍要自动改两稿 | Critique→Improve + Compliance 门 |

---

## Q1. 场景：领导要「多智能体平台」一页 PPT

**场景**  
你在面试中介绍 PolicyFlow。面试官说：「听起来像 multi-agent，你们几个 Agent 怎么协作？有没有用 CrewAI / AutoGen？」

### 参考解答（口述结构）

1. **定位一句话**  
   这是**统一编排的 tool-using RAG**，不是角色群聊式 multi-agent 平台。

2. **真实拓扑**  
   - `MemoryLoad`（service）→ `Router`（结构化路由）→ `Retrieval`（**Service，不是伪 Agent**）  
   - 可选 `Skill`（业务规程）→ **`Answer` 主 agent（function calling 工具环）**  
   - `Verifier` / Compliance 规则门 → Memory writeback  
   - 可选高风险路径上的 Critique→Improve 反思闭环（硬轮次，不是 peer 辩论）

3. **和 CrewAI 的差别**  
   - 我们不跑「多角色互相发消息」的 actor 总线  
   - 决策集中在 Supervisor 式 `AgentPipeline` / 统一 chat 编排  
   - Chat 与 Eval **同一编排语义**，避免双写 stage 导致演示和评测两套故事

4. **可称 Agent 的边界**  
   - 允许：有结构化决策、工具环或明确质量门的节点（Router / Answer / 可选 Reflection）  
   - 不允许：关键词 if 空壳、Retrieval 硬叫 RetrievalAgent 当「智能体」吹

5. **必说边界**  
   - 静态拓扑 + 中心化编排；**不是**分布式状态机 / peer multi-agent  
   - TurnState 是**单轮请求内**的黑板与错误账本，不是跨进程 actor 系统

### 可指代码 / 文档

- `backend/app/agents/pipeline.py`、`router_agent.py`、`answer_agent.py`  
- `docs/08` §3 多智能体诚实落点、`docs/interview/02-architecture`

### 可能追问

| 追问 | 短答 |
|---|---|
| Agent 到底几个？ | 编排上多 stage；**主 agent 是 Answer** |
| 为什么不直接上框架？ | 企业制度问答要可审计、可拒答、可同路径评测；框架默认群聊会掩盖证据绑定 |

---

## Q2. 场景：胡话问题也「像模像样」答了

**场景**  
产品说：「用户问《银河系员工火星差旅补贴》，模型根据通用知识编了三段流程，体验很好。」你怎么处理？

### 参考解答

1. **问题本质**  
   制度问答的核心风险是**幻觉当制度**。体验好 ≠ 合规；无可靠证据必须可拒答。

2. **本项目策略**  
   - 检索无可靠命中 → 默认 **hard refuse**  
   - Compliance / Verifier：`NO_RELIABLE_EVIDENCE`，`passed=false`（可配置，但默认偏硬）  
   - Skill 路径：无证据应 `insufficient_evidence`，**禁止**编清单装流程  
   - 禁止伪造 diagnostics（如假 `skill.suggest` tool trace）

3. **和「软免责声明」的区别**  
   - 软答 +「仅供参考」仍可能被业务当制度执行  
   - 我们把「不能答」做成**产品行为**，而不是 prompt 里一句免责

4. **claim–evidence 边界（主动说）**  
   - 当前 claim–evidence 偏**词重叠规则门**，不是 LLM-as-judge 全文事实核查  
   - 所以 hard refuse + 引用对齐校验比「模型自觉」更靠谱

5. **面试加分表述**  
   > 我宁可用可演示的拒答，也不用好看的幻觉流程。拒答本身是质量特性。

### 可指代码 / 文档

- `backend/app/agents/compliance_agent.py`、`grounding.py`、`skill_agent.py`  
- `docs/08` §2.2 证据绑定、`docs/interview/09-honesty-boundaries`

### 可能追问

| 追问 | 短答 |
|---|---|
| 有证据但答偏了？ | Verifier + 引用 `[n]` 对齐；可选 Reflection 改稿；Compliance 仍是最后一道门 |
| 能否 soft refuse？ | 可配置，但简历/面试默认讲 hard refuse 主路径 |

---

## Q3. 场景：简历写「Hybrid 显著优于 BM25」

**场景**  
面试官指着你的简历：「Hybrid Hit@1 比 BM25 高 20 个点——实验设置是什么？N 多少？有没有干扰文档？」

### 参考解答

1. **先交实验协议，再谈数字**  
   主指标必须带齐：**Hit@1 / Hit@5 / Hit@10 / MRR + 检索策略名 + N**。  
   没有策略与 N 的百分比，我默认当不可比。

2. **本项目评测约定**  
   - 金标：CRUD `questanswer_*`（**不是** `80000_docs` 正文当 QA）  
   - 语料只进专用库：`code=eval_test`，名「测试库」；**禁止**灌 HR/财务业务库  
   - 导入支持**干扰文档**（建议 ≥200），避免小库 + 1-doc 金标虚高 100%  
   - Run 优先**随机 50 / 100**，多策略对比时全量几百条会成倍变慢且难讲清

3. **Hybrid vs BM25 怎么讲才诚实**  
   - Hybrid = 多路（如 BM25 + 向量）+ 融合（如 RRF）+ 可选本地 lexical rerank  
   - 在 **1-doc 整篇匹配**任务上，Hybrid 与 BM25 **接近是常见现象**  
   - **无区分度时绝不写「Hybrid 显著更优」**；简历只写「可对比、可导出、写清策略」

4. **Chat 与 Eval 同语义**  
   - 避免「线上 Hybrid、评测另一套」  
   - 统一 `AgentPipeline` 编排，保证 Hit@K 解释得了线上行为

5. **被质疑刷分时的态度**  
   > 若只有无干扰小样本 100%，那是评测失效，不是系统变强。我们用专用库 + 干扰 + 随机 N 抗这个问题。

### 可指代码 / 文档

- `backend/app/evals/retrieval_metrics.py`、`eval_runner.py`、`eval_dataset_import.py`  
- `docs/interview/06-eval-metrics`、`CLAUDE.md` 评估约定

### 可能追问

| 追问 | 短答 |
|---|---|
| Hit@K 怎么算？ | gold `document_id` ∩ top-k；MRR=1/rank_first_hit；多文档另有 HitAll@K |
| RAGAS？ | 可选；有依赖真跑否则 proxy；**主指标仍是 Hit@K/MRR** |
| LightRAG 分数？ | 可能 rank decay 合成分，`score_is_synthetic` 时不当真相似度 |

---

## Q4. 场景：Skill / Tool / MCP 分不清

**场景**  
面试官：「你们 Skill 和 Tool 有什么区别？MCP 是不是就是又包了一层 Tool？」

### 参考解答

1. **三层定义（诚实版）**

| 层级 | 是什么 | 不是什么 |
|---|---|---|
| **Tool** | 原子、可审计能力；有 `ToolCallLog`；供 Answer 工具环调用 | 不是业务流程本身 |
| **Skill** | **证据绑定**的业务规程（如差旅流程清单）；可调 LLM/证据 | 不是假 suggest 轨迹；无证据不编造 |
| **MCP** | **外部协议适配**（stdio/http 真客户端） | 不是「又一个 Tool 注册名」；企业 SaaS 可 mock 但必须标注 |

2. **主路径谁在用**  
   - Answer 是主 agent，function calling 进 Tool  
   - Skill 在 Router 判定需要规程时执行，结果可回灌 Answer  
   - MCP 用于外部连接；本地 demo stdio 可真连（如 echo / time_now）

3. **面试必说边界**  
   - 企业连接器缺租户密钥 → mock adapter，响应带 **`status=mock`**  
   - 禁止把 mock 画成「已真实发飞书/邮件」  
   - 禁止 diagnostics 伪造 `skill.suggest:*`

4. **一句话记忆**  
   > Tool 是动词原子；Skill 是带证据的业务剧本；MCP 是出边界的协议插头。

### 可指代码 / 文档

- `backend/app/tools/`、`skills/`、`mcp/`  
- `docs/interview/04-agent-skill-tool-mcp`、`docs/09` Tool/MCP 演示段

### 可能追问

| 追问 | 短答 |
|---|---|
| Skill 失败怎么办？ | `insufficient_evidence` / 错误进 TurnState ledger + diagnostics，不静默吞 |
| 为什么 MCP 不全真？ | 协议层真；缺企业密钥的一侧 mock 并标注，比假「已对接生产」诚实 |

---

## Q5. 场景：用户只说「给我模板」

**场景**  
上一轮用户问了「入职体检报销流程」，下一轮只发：「给我模板」。检索如果只拿这四个字，会漂到无关文档。

### 参考解答

1. **问题**  
   短跟进句缺主题；naive RAG 会丢指代，导致拒答或答飞。

2. **本项目做法**  
   - **Query rewrite**：结合近期对话，把「给我模板」扩成带主题的检索查询  
   - 四层记忆装配：L0 全量 messages；L1 近窗 + 滚动摘要；需要时 L2/L3 召回  
   - 冷热是 **prompt 装配策略**，不是物理把窗外消息删掉

3. **模板类意图的产品规则**  
   - 用户要模板/表单/清单：在**已有证据**上合成可填写结构  
   - **不要**仅因证据正文没有「模板」二字就 hard refuse  
   - 仍禁止无证据空想 HR 表格字段

4. **与记忆的边界**  
   - 记忆可补「用户偏好用表格」  
   - 记忆**不能**补「制度规定必须三联单」——那必须来自本轮 RAG 证据

5. **交互**  
   - 优先 `POST /api/chat/stream` SSE：记忆加载 → rewrite → 检索 → 回答 → writeback  
   - 前端助手 Markdown；可复制；短链路可演示

### 可指代码 / 文档

- `backend/app/services/query_rewrite.py`、`memory_window.py`、`memory_service.py`  
- `routes_chat.py`、`docs/interview/05-memory-context`

### 可能追问

| 追问 | 短答 |
|---|---|
| rewrite 用规则还是 LLM？ | 以当前实现为准；目标是结构化/LLM 增强，面试说「结合历史消解指代」即可，并承认可继续加强 |
| 窗口多长？ | 近窗 K 轮 + summary；具体 K 以配置/代码为准，强调装配而非死记数字 |

---

## Q6. 场景：记忆把过期制度当真理

**场景**  
用户上周说「我们部门差旅一律先找 VP 签字」。本周制度已改。模型把记忆里的这句话写进正式答复，还说「根据您之前说的…」。

### 参考解答

1. **原则**  
   **记忆非权威**。L1/L2/L3 只服务连续对话与偏好，**不能覆盖本轮 RAG 证据**，更不能当制度条款。

2. **分层怎么防**  
   - L0：原始 messages（审计真相源之一）  
   - L1：近窗 + `conversations.summary`（压缩，非制度库）  
   - L2：`memory_items` 事件向量摘要  
   - L3：entity upsert（偏好/实体）  
   - Prompt **分区**：记忆区明确「非制度依据」；制度区只放本轮检索证据

3. **写入策略**  
   - **preference 禁止写入制度条款措辞**  
   - 低 salience `conversation_fact` 有 TTL；STM 卸载事件更短 TTL  
   - preference / entity 可长期，但答复仍要证据门

4. **排序（能讲公式就讲）**  
   LTM 召回：`relevance × (0.55 + 0.35·importance + 0.10·recency) + access_boost`  
   （本地公式；`MEMORY_RANK_DECAY_LAMBDA` 可调）  
   → 强调这是**工程启发式**，不是学术 SOTA Memory OS。

5. **管理面**  
   - `GET/DELETE /api/memory` + 前端 `/memory`（仅本人）  
   - 用户可清掉错误偏好——可演示的「可纠正」

6. **冷热误区**  
   - 冷热 = 装配（hot 近窗 / warm 摘要+固定偏好 / cold→selected top-k）  
   - **不是**独立冷热存储，窗外 raw 仍在 L0

### 可指代码 / 文档

- `memory_service.py` / `memory_extractor.py` / `memory_window.py`  
- `docs/interview/05-memory-context`、CLAUDE.md 记忆约定

### 可能追问

| 追问 | 短答 |
|---|---|
| 和 Mem0/长期记忆产品比？ | MVP 四层装配 + 可审计；不宣称 Memory OS / 生产级记忆中台 |
| 如何评记忆质量？ | 当前主评测在检索 Hit@K；记忆以规则+体验+单测为主，不夸大 |

---

## Q7. 场景：评测 100% Hit@1 被质疑刷分

**场景**  
你导出一张表：Hybrid Hit@1=1.0，N=10。面试官笑了：「是不是金标文档库里只有那几篇？」

### 参考解答

1. **承认该笑**  
   无干扰、N 极小、金标与库 1:1 时，Hit@1=100% **没有信息量**。

2. **我们怎么防虚高**  
   - 专用 **eval_test**，不污染业务库  
   - 导入干扰文档，扩大候选噪声  
   - 随机采样 50/100 报 N  
   - 多策略对照（Hybrid / BM25 / …），看相对差而不是绝对满分  
   - 索引后台排队，避免「导入转圈」导致只敢跑微型集

3. **指标解释口径**  
   - Hit@K：金标文档是否进 top-k  
   - MRR：首个命中位置倒数  
   - 多文档任务用 HitAll@K，避免只报单点运气  
   - 主看板聚焦 Hit@1/5/10/MRR；逐条 debug 折叠

4. **简历怎么写**  
   - 写：策略、N、是否干扰、任务类型（如 questanswer_1doc）  
   - 不写：脱离设置的「SOTA / 100% 准确率」  
   - Hybrid 无优势时如实写接近，体现判断力

5. **同路径**  
   Chat 与 Eval 同编排，避免「评测换特殊检索器刷分」。

### 可指代码 / 文档

- `frontend` 评估中心、`eval_service.py`、`docs/09` Eval 演示段  
- `docs/interview/06-eval-metrics`

### 可能追问

| 追问 | 短答 |
|---|---|
| 为何不用全量几千条每次？ | 多策略×全量墙钟爆炸；随机 N 可复现对比，全量适合发版回归 |
| 金标从哪来？ | CRUD 公开 split 的 questanswer_*；路径见项目约定 |

---

## Q8. 场景：「你们做了 cross-encoder 重排？」

**场景**  
简历写了 Rerank。面试官：「用的 BGE-reranker 还是 Cohere？模型多大？延迟多少？」

### 参考解答

1. **立刻纠偏**  
   默认 **不是** cross-encoder / 不是云端 rerank API。  
   默认是 **`local_lexical_fusion`（本地词法融合重排）**。

2. **为什么这样选（MVP 叙事）**  
   - 单机可演示、无额外 GPU/API 依赖  
   - metadata 可核验，评测可开关对比  
   - 面试场景优先**可复现与诚实**，而不是不可演示的大模型链路

3. **怎么讲价值**  
   - 在 hybrid 多路融合后做二次排序，压噪声  
   - 与「不做 rerank」可 A/B；数字说话  
   - 明确：**扩展 cross-encoder 是下一阶段**，不是当前叙事的一部分

4. **关联诚实点**  
   - LightRAG 分可能 synthetic，和 rerank 分数含义不同，勿混谈  
   - Rerank 开关与策略名应进 eval 结果，避免口头与导出不一致

5. **态度模板**  
   > 我把 rerank 做成可开关的工程能力，并在文档写死实现级别。需要 cross-encoder 时是加模型与评测回归，不是改 PPT 用词。

### 可指代码 / 文档

- `backend/app/rag/rerank_service.py`  
- `docs/interview/03-rag-retrieval`、`09-honesty-boundaries` 禁用词表

### 可能追问

| 追问 | 短答 |
|---|---|
| 词法 fusion 会不会伤语义召回？ | 可能；所以可关，且用 Hit@K 看净收益 |
| 线上默认开吗？ | 以配置/实现为准；面试强调「可配置 + 可评测」 |

---

## Q9. 场景：前端一直转圈 / 不知道在干什么

**场景**  
HR 试用时说：「点发送后转圈 20 秒，不知道卡死了还是在想。诊断信息一展开全是 JSON，吓人。」

### 参考解答

1. **产品判断**  
   RAG+工具环延迟高于普通 IM；必须把**阶段进度**变成可感知反馈，而不是假进度条或巨量 debug。

2. **本项目交互**  
   - 主路径 SSE：`POST /api/chat/stream`  
   - 阶段示例：记忆加载 / query rewrite / 检索 / 回答 / writeback  
   - UI：**安静 status chips** + 可折叠执行过程 + 渐进时间线（避免刷屏）  
   - 回答：Markdown、底部复制；用户气泡悬停复制/编辑；打开滚到最新；空状态示例问题

3. **信息层级**  
   - 用户默认只看：阶段名 + 最终答案 + 引用  
   - diagnostics / 工具轨迹给演示与排障，**不默认铺满主气泡**  
   - 评估页同理：主指标突出，次要配置与逐条结果折叠

4. **视觉与工程边界**  
   - 软 mint 画布 + 白浮动卡片等（见 UI 约定）是体验层  
   - **不是**生产级 IM（已读、推送、多端同步等不做夸大）

5. **和后端的关系**  
   - 阶段事件应来自真实编排进度，不为好看伪造 stage  
   - 错误集中进 TurnState / diagnostics，前端可展示失败阶段而不是无限转圈

### 可指代码 / 文档

- `frontend/src/features/chat/`、`routes_chat.py`  
- `docs/interview/08-frontend-ux`、记忆/聊天 UX 约定

### 可能追问

| 追问 | 短答 |
|---|---|
| 非流式呢？ | `POST /api/chat` 保留；演示优先 stream |
| 如何防 SSE 断流？ | 工程上可重连/降级非流式；面试可承认 MVP 边界并讲已做的阶段事件 |

---

## Q10. 场景：高风险回答仍要自动改两稿

**场景**  
合规希望：涉及「解雇 / 公积金比例 / 对外承诺」时，系统自动找茬再改一版再出门。你怎么设计才不像「两 Agent 吵架演戏」？

### 参考解答

1. **已做能力（诚实命名）**  
   - 可选 **Critique→Improve 反思闭环**  
   - 双 prompt + **硬轮次上限**（如 max 2）  
   - **高风险触发**；Eval 路径默认关，避免评测噪声与成本爆炸  
   - **不是**事实 oracle，**不是** peer multi-agent 辩论

2. **在流水线中的位置**  
   - Answer 出稿 →（条件）Critique → Improve → 再过 **规则 Compliance / Verifier**  
   - Reflection **不能替代**证据门：无证据仍应 fail，不靠「改写得更像制度」

3. **TurnState 黑板**  
   - 单轮内集中状态与 `errors[]`  
   - 步骤/检索/Skill/合规错误写入 ledger + diagnostics  
   - 便于解释「第几轮 improve、为何仍拒绝」

4. **为何这样收敛**  
   - 群聊式互评难审计、难复现、难与 Hit@K 同叙事  
   - 硬轮次 + 规则门 = 可讲清的工程闭环  
   - 成本与延迟可控，适合演示与实习 MVP

5. **边界金句**  
   > 反思环提高的是**表述与自检覆盖**，不保证事实正确；事实仍靠检索证据 + hard refuse + compliance。

### 可指代码 / 文档

- `backend/app/agents/reflection_loop.py`、`critique_agent.py`、`improve_agent.py`  
- `docs/08` 落地状态、`docs/interview/09-honesty-boundaries` Reflection 条

### 可能追问

| 追问 | 短答 |
|---|---|
| 和 ToT/CoT 关系？ | 另有按难度 CoT/ToT 与用户路径选择；与 Critique 环是不同机制，勿混成一个「万能推理」 |
| 会不会无限改？ | 硬 max 轮；到上限交 Compliance 裁决 |

---

## 附：10 题速记卡（面试前 5 分钟）

| # | 一句话结论 | 必说边界 |
|---|---|---|
| 1 | 统一编排 tool-using RAG，不是群聊 multi-agent | 无 peer 总线 |
| 2 | 无证据 hard refuse | claim 门非 LLM 法官 |
| 3 | Hit@K/MRR + 策略 + N | 1-doc 上 Hybrid 未必碾压 BM25 |
| 4 | Tool 原子 / Skill 规程 / MCP 协议 | mock 带 status |
| 5 | 短跟进靠 rewrite + 历史 | 记忆不顶制度 |
| 6 | 记忆非权威 + TTL/偏好禁条款 | 冷热=装配 |
| 7 | eval_test + 干扰 + 随机 N | 禁业务库灌金标 |
| 8 | 本地 lexical fusion | 非 cross-encoder |
| 9 | SSE 阶段 + 安静 chips | 非生产 IM |
| 10 | Critique→Improve 硬轮次 | 非辩论；Compliance 终局 |

---

## 维护

- 改编排 / 检索 / 记忆 / Eval / Reflection 表面行为时：同步改本题解答与 [`../09-honesty-boundaries`](../09-honesty-boundaries/README.md)  
- 新增场景题：只写代码里有的；规划中能力放「未做」  
- 现场数字以你最近一次 eval 导出为准，本文**不锁死**具体 Hit 分数  
