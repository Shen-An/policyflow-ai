# 02. 整体架构与编排诚实性

## 核心论点（先背这个）

**Chat 与 Eval 走同一编排 `AgentPipeline`，不双写 stage。**  
检索是 **Service**，不是伪 Agent；主 agent 是 **Answer + tool loop**。

## 白话详解（这两句话到底什么意思）

上面那两句话太凝练,这里拆开讲。面试官一追问「这个 Service 到底是什么」「路由怎么做」,你照下面答。

### 「检索是 Service」是什么意思？

**一句话:检索就是一个「你给我问题,我去资料库翻,翻完把相关段落还给你」的工具人。它不自己拿主意。**

打比方:你去图书馆,前台有个图书管理员。你说「我要年假相关的制度」,管理员就去书架翻,翻完抱一摞文件回来。管理员不会问你「你真需要这个吗」,也不会自己决定「我觉得你还该看看报销制度」——他只管翻书、给你结果。

代码里就是这么回事:

- `RAGService`(`backend/app/services/rag_service.py`)就是这图书管理员。它有个 `retrieve()` 方法,你传进去一个问题 + 用哪种检索方式(Hybrid / BM25),它就去查,查完返回一堆 `Evidence`(证据段落)。
- `RetrievalAgent`(`backend/app/agents/retrieval_agent.py`)是更薄的壳——它只做一件事:调 `RAGService.retrieve()`,顺便记一下「这次检索花了多久、有没有超时」。它**没有**自己的大脑,不做任何决策。

为什么特意说「不是伪 Agent」?因为很多项目为了听起来高级,把每一步都包装成「智能体」——好像检索也是一个有自己想法的 AI 在跟别的 AI 开会。我们没有。检索就是**流水线上一个固定工序**:上一步(路由)告诉它查什么,它去查,查完交给下一步(回答)。**它不思考、不聊天、不自主决策。**

真正有「大脑」的是 `AnswerAgent`——它会判断「信息够不够回答」「要不要再调个工具补充」,这才是真正在做决策的 AI。

▸**面试可以说**:「检索不是 Agent,就是个 Service:给查询条件就去资料库翻、翻完还一堆段落,不做决策。真正会决策的是 Answer Agent——它判断信息够不够、要不要再调工具。」

### 路由(Router)是怎么做的?

**一句话:路由就是「每句话进来先分诊」——像医院挂号台,先判断这问题属于哪个科、严不严重、要不要走专家通道(Skill),然后给后面的人一张单子。**

分诊判断 5 件事(对应代码里 `RouterResult` 的字段):

| 判断什么 | 字段 | 大白话 | 举例 |
|---|---|---|---|
| 哪个领域 | `domain` | 归哪个知识库 | 问年假 → `hr`;问报销 → `finance` |
| 什么类型 | `task_type` | 简单问答 / 要清单 / 要对比 / 要写草稿 | 「年假几天」→ `knowledge_qa`;「出差报销流程」→ `process_checklist` |
| 难度 | `difficulty` | 直答 / 分几步 / 要分叉选路 | 「年假几天」→ `simple`;「先查年假再对比出差补贴」→ `multi_step` |
| 要不要 Skill | `need_skill` | 要不要用业务规程整理成清单/对比表 | 要清单 → `true`;简单问答 → `false` |
| 风险等级 | `risk_level` | 涉不涉法律/合规/财务 | 问处罚 → `high`;问年假 → `low` |

**怎么判断?两条路:**

**第一条路(主力):让大模型判断。** Router 给大模型发一段提示词(代码 ~191 行),大意是「你是企业制度问答路由器,只输出 JSON,判断这几个字段」。大模型看完问题吐回一个 JSON,Router 解析。比如「出差报销流程和年假申请有什么区别」,大模型判断:这是 `policy_compare`、需要 Skill、`multi_step`。

**第二条路(兜底):关键词匹配。** 大模型没配、或返回的东西解析不了时,用简单关键词规则分诊(代码 ~33 行):问题里有「流程/步骤/清单」就归 `process_checklist`;有「对比/区别」就归 `policy_compare`;有「法律/合规/处罚」就标 `high`。不如大模型准,但保证不崩。

**路由完之后呢?** Router 返回一张「分诊单」(`RouterResult`),流水线照着走:
- 简单问答 → 直接检索 → 回答
- 要清单 → 检索 → 调 Skill 整理成清单 → 回答
- 多步问题 → 拆成几个子步骤,可能分波次并行执行

▸**面试可以说**:「路由就是分诊台。每句话进来先判断:归哪个库、什么类型、难度多大、要不要 Skill、风险等级。主力用大模型做结构化判断,大模型不可用退到关键词规则兜底。判断完输出一张分诊单,后面流水线照着走。」

### 打个 Java 的比方（帮记忆）

如果你熟 Java/Spring,这套分层是同源的:

| PolicyFlow                     | Java 类比                         | 说明                          |
| ------------------------------ | ------------------------------- | --------------------------- |
| `routes_chat` + `chat_service` | Controller(HTTP 入口)             | 接请求、鉴权、落库                   |
| `AgentPipeline`                | 编排/调度层(介于 controller 和 service) | 调度各阶段,不自己干活——**不是** HTTP 入口 |
| `RAGService`                   | Service(被调用的工具人)                | 无状态:给请求→还结果;不管事务/持久化        |
| `db/`(SQLModel)                | DAO                             | 数据访问                        |

**路由那个 LLM + 关键词兜底**,就相当于「先让一个智能判断器分诊,判断器不可用就退到一套 if-else 关键词规则」——而且判断器的输出还会被代码校验一遍(domain 白名单、task_type 合法性、难度自提升),不是它说啥就是啥,可审计。

---

## 主路径（白板可画）

```text
用户提问
  → MemoryLoad（非权威）
  → query rewrite（短跟进补主题）
  → Router（只分诊，不查库）+ plan_normalize（校验难度/CoT/ToT）
  │   simple：直走下面 ｜ multi_step：进 PlanExecutor 分波次（retrieve/skill 可并行）｜ branched：先 ToT 选路
  → Retrieval Service（Hybrid/BM25；LightRAG 超时→BM25 打标）   ★ Service 第1次
  → 检索质量门 ──retry──→ 拿【原问题】再调一次 Retrieval（★ Service 第2次·重检）→ 再判质量门
  │                 └─ 跑题（命中但 overlap 太低）→ 直接清空证据（不重检）
  │                 └─ 仍 refuse → 清空证据
  → Skill?（吃证据出清单/对比；无证据 insufficient_evidence）
  → Answer（tool loop；Answer 觉得不够→调 kb.search→同一个 RAGService ★ 第3次可能）
  → Compliance 发布门（PASS / REVISE→改稿复检 / REFUSE→安全拒答）
  → MemoryWriteback
（整轮 Turn Budget：llm=16 / retrieval=2 / tool=8 / 180s，超限收口）
```
### 决策 vs 执行(谁拍板、谁干活)

**决策在 Router + plan_normalize,执行在 pipeline。** Router 是结构化路由——配了 LLM 才用 LLM 判断,否则退到关键词规则;它输出 `RouterResult`(库 / 类型 / 难度 / 要不要 Skill)是施工依据。`plan_normalize` 再做一次确定性校验和覆盖(难度→CoT/ToT、拆步)。最后 pipeline 拿着定好的单子派活:simple 直接检索+Answer;need_skill 先让 Skill 出结构、再让 Answer 汇总;branch 走 ToT 生成候选让用户选路才执行。

▸**面试可以说**:「决策和执行分开:Router 分诊 + plan_normalize 校验定难度/CoT/ToT/拆步;AgentPipeline 只按定好的 plan 派活。pipeline 不是那个拍板去哪个服务的角色。」

### 为什么是「先检索 → 再 Skill」这个顺序?

**核心:数据依赖。Skill 的输入就是检索到的证据,没证据它没东西可整。**

看 `skill_agent.py` 的 `execute_one`,`evidence: list[Evidence]` 是必传参数;三个 skill 全把证据当原料:

- `process_checklist`:在证据上生成流程清单(payload 带 `evidence`);
- `policy_compare`:从证据拼成对比项,**少于 2 条证据直接 skip、不硬做**;
- `summary`:把证据的 snippet 拼成文本再总结。

所以顺序不是随便定的——**Skill 必须吃检索结果才有材料**。反过来先 Skill 后检索,Skill 没证据只能 `insufficient_evidence`,啥也出不来。

第二个原因:**证据绑定(诚实红线)**。清单/对比表是基于真实查到的制度段落生成的,不是 Skill 自己编的。先 Skill 后检索等于让 Skill 凭空编清单——正是要防的幻觉。

**打比方**:Skill 是个「照着资料整理成表格」的文员。你得先把资料(检索)递到他手里,他才能整;没资料他只能说「没材料,做不了」,绝不会自己编一份表。

▸**面试可以说**:「先检索后 Skill 是数据依赖:Skill 的输入就是检索到的证据,没证据它只能 insufficient_evidence。这也保证证据绑定——清单基于真实制度段落,不是 Skill 编的。policy_compare 甚至少于 2 条证据直接跳过。」
### Service 到底在哪起效？（最容易看漏的点）

**Service（检索服务）在路由之后才跑,不是之前。而且有两次可能起效:**

1. **第 1 次（必跑）**:Router 分诊完 → 拿着「查什么、查哪个库」去资料库翻 → 拿回一堆证据段落。这是主流程固定的一步。*Router 只管判断,不碰资料库。*

2. **第 2 次（可选,看情况）**:Answer 主 agent 在 tool loop 里,如果觉得「翻回来的不够」,会调 `kb.search` 工具(也叫 `retrieve`/`search`)再查一次——**这个工具内部调的还是同一个 RAGService**。

所以「检索是 Service」不光是说它是个工具人,还意味着:**主流程查一次,Answer 工具环里还能再查,走的是同一套检索代码**——这就保证了「在线问答」和「评测」用的是同一套检索逻辑,数字能对上叙事。

▸**面试可以说**:「检索在路由之后才跑,不是之前。路由只分诊不查库。Service 第一次在主流程固定查一次;Answer 觉得不够时还能通过 kb.search 工具再调同一个 Service 查。两次走同一套检索代码,所以在线和评测的检索逻辑是一致的。」

---

## 关键代码

| 组件        | 路径                                                                  | 面试怎么说             |
| --------- | ------------------------------------------------------------------- | ----------------- |
| 统一编排      | `backend/app/agents/pipeline.py`                                    | Chat/Eval 同路径     |
| Router    | `backend/app/agents/router_agent.py`                                | 结构化路由，不是聊天群       |
| Retrieval | `backend/app/agents/retrieval_agent.py` + `services/rag_service.py` | 检索服务封装            |
| Answer    | `backend/app/agents/answer_agent.py`                                | 主 agent + tools   |
| Memory    | `backend/app/agents/memory_agent.py`                                | load/writeback 组件 |
| Chat 入口   | `backend/app/services/chat_service.py`                              | SSE stage + 非流式   |

## 是 multi-agent 吗?(最容易讲错的一节,先看这个)

**准确口径:是 multi-agent(多角色),不是 autonomous multi-agent(群聊自主)。加俩词就诚实了。**

很多人(包括我一开始)看到「不是玩具 multi-agent」就记成「不是 multi-agent」——错。代码里这些**都叫 agent**:`RouterAgent` / `RetrievalAgent` / `SkillAgent` / `AnswerAgent` / `ComplianceAgent` / `CritiqueAgent` / `ImproveAgent` / `MemoryAgent`。从「系统里有多个承担不同职责的 agent 角色」这个角度——**是的,是 multi-agent。** 要划清的是「哪一种」:

| 种类 | 长啥样 | 我们是不是 |
|---|---|---|
| **群聊式自主 multi-agent** | 多个有自己目标的 agent 互相发消息协商(CrewAI/AutoGen 那种) | **不是** |
| **多角色编排 + 中心化 + 波次内并行** | 多个 agent 角色,由一个总指挥按固定拓扑调度,只在独立步骤上并行 | **是** |

**串行在哪**(主拓扑,固定顺序):

```text
Router → Retrieve → Skill → Answer → Compliance → MemoryWriteback
```
波次之间也是串行(一波跑完才下一波);`answer / tool / verify` 永远单独成波,不跟别人并。

**并行在哪**(`plan_executor.py`,**真的并发**,不是假装):

- 只有 `retrieve` 和 `skill` 这两类**可以并行**(`_PARALLELIZABLE`)。
- `answer / tool / verify` 永远串行(`_SERIAL_KINDS`),因为它们要么是收尾、要么有副作用、要么对用户可见。
- 同一波次里多个**无依赖**的步骤,用 `asyncio.gather` 真并发跑。
- *例:一个多步任务要查 HR 库又查财务库,两个 retrieve 没依赖关系 → 并行查;查完再串行交给 Answer。*

**关键区别(诚实红线)**:我们的 agent **不互相发消息开会**,而是往同一本 `TurnState`(黑板)写结果,由 `AgentPipeline` 中心调度。并行只发生在「独立、无依赖」的步骤上,不是任意 agent 乱序聊天。代码文件头自己写死了立场(`plan_executor.py` 第 1-4 行):

```python
"""L2 PlanExecutor: ... Still a centralized service (not peer multi-agent).
Parallelism is only for independent ready steps (typically multiple retrieve / independent skills)."""
```

▸**面试可以说**:「是 multi-agent,但不是群聊式自主 multi-agent。我有多个 agent 角色:Router 分诊、Retrieval 检索、Skill 出清单、Answer 主决策带 tool loop、Compliance 把关、Critique/Improve 反思改稿。拓扑是中心化编排:AgentPipeline 按固定阶段调度,各阶段往同一本 TurnState 写结果,不互相发消息。串行是主拓扑,但 PlanExecutor 会在独立步骤上真并行——比如多个无依赖的 retrieve 用 asyncio.gather 并发查。所以准确说法是:多角色编排 + 中心化 Supervisor + 波次内并行,不是 CrewAI 那种 peer 群聊。」

## 为什么说「不是玩具 multi-agent」

1. **没有**多个独立 LLM agent 互相发消息「开会」——是中心化编排 + 黑板共享,不是 peer 群聊(详见上一节)  
2. 有明确 **数据契约**：RouterResult / Evidence / SkillResult / AnswerResult  
3. diagnostics 只记 **真实 stage / tool**，禁止伪造 `skill.suggest:*`  
4. Skill 无证据时返回 `insufficient_evidence`，不编清单  
5. 评估与在线问答共用检索与编排逻辑，指标可对上叙事  

## 总指挥 + 固定步骤 + 一本共享记录（面试白话）

| 说法         | 代码对应                                                                           | 诚实边界                                                                      |
| ---------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| **总指挥**    | `AgentPipeline`（Supervisor）                                                    | 中心化编排，不是 peer multi-agent                                                 |
| **固定步骤**   | Router → Plan → Retrieve/Skill → Answer → Compliance；L2 按 `plan_steps` + waves | 拓扑**静态为主**；动态只在边界（ToT 用户选路、依赖波次内并行）                                       |
| **一本共享记录** | `TurnState`（`backend/app/agents/base.py`）                                      | 本轮 blackboard：question / plan / evidence / skills / answer / **errors[]** |
| **成功失败都记** | `TurnState.record_error` / `record_step_outcome`；`PipelineResult.errors`       | 步骤 error/skip、无证据、合规告警等写入 ledger；diagnostics 可带 `errors`                  |

**怎么说（30 秒）：**  
「我们是静态拓扑：总指挥按固定阶段跑。各阶段不互相发消息，而是往同一本 `TurnState` 写结果。失败也写进去，后面阶段和 diagnostics 能读到，不是静默吞掉。」

**不要夸大：**  
- 不是分布式 actor / 消息总线；共享状态是**单轮请求内**的 Pydantic 对象。  
- 不是完整「状态机引擎」；`TurnState` 是黑板 + 错误账本，编排逻辑仍在 `pipeline.py` / `plan_executor.py`。  
- 并行只发生在 PlanExecutor **同一波次、无依赖**的步骤上，不是任意 agent 乱序通信。

## 上下文防腐（架构原则）

来自 `docs/01` §8：

1. 历史对话不能替代制度检索  
2. 用户偏好不能覆盖知识库证据  
3. 草稿/清单必须用本轮证据  
4. 会话摘要区分事实与待确认（实现上滚动摘要 + 事件抽取）  
5. 记忆 working set 标 **non-authoritative**

## 可能被追问

**Q: Router / Retrieval / Memory 都叫 Agent，是不是 multi-agent？**  
A: 命名历史；运行时它们是 pipeline stage / service。主决策与 tool loop 在 Answer。面试主动说清「编排阶段 ≠ 自主 agent 群」。

**Q: 为什么不做成 CrewAI/AutoGen？**  
A: 企业制度问答要可控、可审计、可评测；群聊式 agent 难保证证据绑定与拒答一致性。

**Q: 子 agent 怎么通信？错误怎么处理？**  
A: **不 peer 通信**。阶段通过 `TurnState` 共享本轮记录（共享状态 / blackboard）。步骤失败、无 KB、无可靠证据、Skill 失败、合规告警等走 `errors[]` 集中写入；`PipelineResult` 带回同一账本。不是「每个 agent 私聊再汇总」。

## 相关章节

- Skill/Tool/MCP 细节 → [04](../04-agent-skill-tool-mcp/README.md)
- 检索细节 → [03](../03-rag-retrieval/README.md)
- 诚实边界 → [09](../09-honesty-boundaries/README.md)
