# 04. Agent / Skill / Tool / MCP 诚实分层

## 分层定义（必须能脱口而出）

| 概念 | 是什么 | 不是什么 |
|---|---|---|
| **Tool** | 原子、可审计的能力调用（检索补充、草稿、memory.read/write…） | 业务完整流程 |
| **Skill** | 证据绑定的业务规程（清单/对比/摘要…） | 无证据也能编的「智能」 |
| **MCP** | 真协议客户端（stdio/http）；企业 SaaS 可 mock | 「写了个 JSON 就叫 MCP」 |
| **Answer Agent** | 主 agent：function calling 工具环 | 唯一「会思考」的 LLM 外壳 |

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