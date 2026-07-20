# 02. 整体架构与编排诚实性

## 核心论点（先背这个）

**Chat 与 Eval 走同一编排 `AgentPipeline`，不双写 stage。**  
检索是 **Service**，不是伪 Agent；主 agent 是 **Answer + tool loop**。

## 主路径（白板可画）

```text
persist user
  → MemoryLoad（非权威 working set）
  → query rewrite（短跟进补主题）
  → Router（结构化 domain/task/need_skill）
  → Retrieval Service（Hybrid / BM25 …）
  → Skill?（证据绑定规程；可 insufficient_evidence）
  → Answer（function calling tool loop）
  → Compliance / Verifier 质量门
  → MemoryWriteback
```

## 关键代码

| 组件 | 路径 | 面试怎么说 |
|---|---|---|
| 统一编排 | `backend/app/agents/pipeline.py` | Chat/Eval 同路径 |
| Router | `backend/app/agents/router_agent.py` | 结构化路由，不是聊天群 |
| Retrieval | `backend/app/agents/retrieval_agent.py` + `services/rag_service.py` | 检索服务封装 |
| Answer | `backend/app/agents/answer_agent.py` | 主 agent + tools |
| Memory | `backend/app/agents/memory_agent.py` | load/writeback 组件 |
| Chat 入口 | `backend/app/services/chat_service.py` | SSE stage + 非流式 |

## 为什么说「不是玩具 multi-agent」

1. **没有**多个独立 LLM agent 互相发消息「开会」  
2. 有明确 **数据契约**：RouterResult / Evidence / SkillResult / AnswerResult  
3. diagnostics 只记 **真实 stage / tool**，禁止伪造 `skill.suggest:*`  
4. Skill 无证据时返回 `insufficient_evidence`，不编清单  
5. 评估与在线问答共用检索与编排逻辑，指标可对上叙事  

## 总指挥 + 固定步骤 + 一本共享记录（面试白话）

| 说法 | 代码对应 | 诚实边界 |
|---|---|---|
| **总指挥** | `AgentPipeline`（Supervisor） | 中心化编排，不是 peer multi-agent |
| **固定步骤** | Router → Plan → Retrieve/Skill → Answer → Compliance；L2 按 `plan_steps` + waves | 拓扑**静态为主**；动态只在边界（ToT 用户选路、依赖波次内并行） |
| **一本共享记录** | `TurnState`（`backend/app/agents/base.py`） | 本轮 blackboard：question / plan / evidence / skills / answer / **errors[]** |
| **成功失败都记** | `TurnState.record_error` / `record_step_outcome`；`PipelineResult.errors` | 步骤 error/skip、无证据、合规告警等写入 ledger；diagnostics 可带 `errors` |

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
