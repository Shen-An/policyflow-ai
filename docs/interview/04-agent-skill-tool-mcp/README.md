# 04. Agent / Skill / Tool / MCP 诚实分层

## 分层定义（必须能脱口而出）

| 概念               | 是什么                                     | 不是什么              |
| ---------------- | --------------------------------------- | ----------------- |
| **Tool**         | 原子、可审计的能力调用（检索补充、草稿、memory.read/write…） | 业务完整流程            |
| **Skill**        | 证据绑定的业务规程（清单/对比/摘要…）                    | 无证据也能编的「智能」       |
| **MCP**          | 真协议客户端（stdio/http）；企业 SaaS 可 mock       | 「写了个 JSON 就叫 MCP」 |
| **Answer Agent** | 主 agent：function calling 工具环            | 唯一「会思考」的 LLM 外壳   |

## 白话详解（这四个到底啥区别）

面试官最爱问这块,因为它最容易绕。用「公司办事」打比方:

| 概念               | 大白话                                                                        | 打比方                                        |
| ---------------- | -------------------------------------------------------------------------- | ------------------------------------------ |
| **Answer Agent** | 真正会动脑的那个。领导(用户)派活,它决定怎么干、要不要用工具、要不要再查资料。                                   | 你这个办事员。                                    |
| **Tool**         | 工具箱里的单件工具。每个只干一件具体事,用完留个记录(可审计)。                                           | 计算器、印章、电话本。「再去查一次库」「存个草稿」「读一下记忆」都是一个 Tool。 |
| **Skill**        | 一套业务办事规程(SOP)。照着检索到的证据,按规程排出一份清单/对比表。**必须有证据,没证据就老实报「证据不足」,不能自己编一份清单。**    | 《差旅报销怎么批》的 SOP 手册。                         |
| **MCP**          | 连外部系统的标准插座。插座规格是真的(stdio/http 真协议),但企业那头(飞书、邮箱)是演示用的假插头,返回的假数据会老实标 `mock`。 | 万能转接头;插飞书那头是假的演示插座。                        |

**四个怎么配合?** 路由(Router)分完诊,如果是要清单/对比的活:

1. 先让 **Skill** 照着检索到的证据,按规程整理出清单/对比表;
2. 同时 **Answer**(主办事员)可以一边想一边去拿工具——调 `retrieve`(再查一次)、`draft`(存草稿)、`memory.read`(看历史),拿到结果继续想,直到能交答案。这就是「tool loop 工具环」;
3. 所有工具调用都**真实记进 diagnostics**,不编假调用、不写装饰性 trace。

**最容易踩的坑(也是诚实红线):**
- 别把 Skill 说成「第二个会自己聊天的 AI」——它只是个规程,没证据就报 `insufficient_evidence`,不编。
- 别把 MCP 说成「已对接飞书生产」——协议层是真的,企业连接器是 mock,响应里写着 `status=mock`。
- 别编假 diagnostics(比如假的 `skill.suggest:*` 调用记录)——只记真实发生过的调用。

▸**面试可以说**:「我这儿只有 Answer 是真正会动脑的。Tool 是单件工具,用完留记录;Skill 是业务规程,照证据排清单,没证据就报不足、不编;MCP 是连外部的标准插座,协议真、企业那头是 mock。」

---

## 运行时关系

```text
Router.need_skill / tool_hints
        │
        ├─► SkillAgent：有证据才结构化；否则 insufficient_evidence
        │
        └─► AnswerAgent tool loop
                ├─ skill.run / retrieve / draft / memory.*
                └─ 真实 tool_trace 进 diagnostics
```

## 关键代码

| 主题 | 路径 |
|---|---|
| Pipeline 编排 | `backend/app/agents/pipeline.py` |
| 共享记录 TurnState / errors | `backend/app/agents/base.py`（`TurnState`、`TurnError`、`PipelineResult.errors`） |
| Plan normalize / branch / executor | `plan_normalize.py`、`plan_branch.py`、`plan_executor.py` |
| Answer + tools | `backend/app/agents/answer_agent.py` |
| Reflection 闭环 | `critique_agent.py`、`improve_agent.py`、`reflection_loop.py`、`schemas/reflection.py` |
| Skill 执行 | `backend/app/agents/skill_agent.py`、`backend/app/skills/` |
| Tool 注册/审计 | `backend/app/tools/`、相关 API |
| MCP | `backend/app/mcp/`（含 stdio demo server） |
| 禁止假 trace | 策略见 `docs/08`；测试见 phase3 |

## 面试金句

1. **Skill = 规程，不是第二个自由聊天 agent**  
2. **无证据 → `insufficient_evidence`，不编步骤清单**  
3. **diagnostics 只记真实调用**，没有 `skill.suggest:*` 装饰性假 trace  
4. **MCP**：本地 stdio 可真连 demo；企业连接器 mock 必须 `status=mock`  
5. **Tool 有权限边界**（如 memory 仅本人 owner）

## 演示怎么点

1. 问流程题：「差旅申请流程有哪些步骤？」  
   - `need_skill=true`  
   - stage：… → Skill → Answer  
   - 回答有清单结构 + `[n]`  
2. 无命中胡话 → hard refuse + `NO_RELIABLE_EVIDENCE`  
3. MCP health-check：`echo` / `time_now`；mock 响应含 `status=mock`

## 追问预案

**Q: Skill 和 Tool 为啥拆开？**  
A: Tool 可复用、可审计；Skill 组合证据与业务步骤。拆开后评测/拒答/权限更好控。

**Q: 工具环最多几轮？**  
A: `CHAT_TOOL_MAX_ROUNDS`（默认 3），有上限，避免无限 function calling。

**Q: 各模块都有 max rounds，会不会合起来仍调用很多次？**  
A: 所以之上还有**请求级 Turn Budget**：单轮总 LLM ≤8、检索 ≤2、Tool ≤6、整轮 ≤180s，任何模块重试都扣同一预算，超限 `TURN_BUDGET_EXHAUSTED` 直接收口。`CHAT_TOOL_MAX_ROUNDS` / `CHAT_REFLECTION_MAX_ROUNDS` 是**局部**限制，Turn Budget 是**全局**上限（软预算，非沙箱）。详见 [11 Q11](../11-scenario-questions/README.md)。

**Q: Tool 调外部系统超时了会自动重试吗？**  
A: **不自动重试**。真实 handler 超时记 `unknown`（不是 `failed`），带稳定幂等键；相同键拒绝盲重发，明确成功的结果可复用。DB rollback 只能撤本地未提交数据，撤不回已发出的邮件 / 飞书；补偿 Tool 与状态查询适配器仍待实现，**不宣称完整 Saga**。详见 [11 Q12](../11-scenario-questions/README.md)。

**Q: 有没有 planner agent？**  
A: **没有开放式 Planner Agent。** Router 做结构化路由，额外输出 `complexity` / `difficulty` / `plan_steps`（用户已编号步骤优先，否则自动拆 2–5 步）；`plan_normalize` 是服务校验。难度三档：`simple`→CoT 直答、`multi_step`→CoT 分步（L1/L2 PlanExecutor，独立子任务可同波并行）、`branched`→**ToT 选路**（生成 2–3 候选计划，双请求 HITL 让用户选路后再执行）。产品 ToT **不是**学术 Tree-of-Thoughts 搜索，仍是中心化 Supervisor，无 peer multi-agent。主 agent 仍是 Answer（tool loop）。复杂度放在可测 stage，不放在 agent 群聊。

**Q: ToT 和 CoT 在你们系统里怎么区分？**  
A: 按任务难度自动分流。简单事实问答走 CoT 直答；多意图/清单走 CoT 分步；存在多种合理执行路径（如对比策略、先 A 或先 B）才升 ToT 选路。用户已写死 1.2.3. 线性步骤不会升 ToT。Eval 不暂停，自动选 recommended。

**Q: 子步骤失败写到哪里？是消息传递还是共享状态？**  
A: **共享状态**。本轮 `TurnState` 是黑板；`record_error` / `record_step_outcome` 把 retrieve/skill/compliance 等失败写入 `errors[]`，`PipelineResult.errors` 与 diagnostics 可透出。不是 agent 之间互发错误消息。

**Q: 有没有自我反思 / self-reflection？**  
A: 有 **闭环反思**，不是模型自夸「我觉得还行」。高风险回答（multi_step/branched、risk medium/high、Skill 清单成功、低置信）在 Answer 之后走 `ReflectionLoop`：  
1. **CritiqueAgent** 只找问题——六个检查维度（证据接地 / 引用 / 数值 / 完整度 / 拒答一致性 / 结构）+ 明确 **PASS** 出口，否则会无限挑毛病或流于表面；  
2. **ImproveAgent** 只按批注 + 原始任务做定向改写，不得编造制度事实；  
3. **`CHAT_REFLECTION_MAX_ROUNDS=2` 硬停**，不依赖模型自己停。  
Critique 与 Improve 是**独立角色/prompt/stage**（即使底层同一 LLM），用来对抗「同一模型对自己输出的自洽偏见」；这不是 peer multi-agent 群聊辩论。规则 **ComplianceAgent** 仍在环后跑，是确定性质量门。无证据 hard refuse **永不**进入反思；Eval 默认 `CHAT_REFLECTION_IN_EVAL=false` 控成本。

**Q: 为啥不每轮都反思？**  
A: 反思要 +1–4 次 LLM 调用。只上关键环节（多步/高风险/清单/低置信），简单高置信事实问答直接跳过并诚实 emit `ReflectionLoop | skipped`。