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
| Answer + tools | `backend/app/agents/answer_agent.py` |
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
A: **没有开放式 Planner Agent。** Router 做结构化路由，额外输出 `complexity` / `plan_steps`（用户已编号步骤优先，否则自动拆 2–5 步）；`plan_normalize` 是服务校验；L2 时 `PlanExecutor` 按 `depends_on` 分波执行，**独立子任务（如不同 query 的 retrieve）同波并行**，有依赖的串行；Pipeline 仍是中心化 Supervisor。主 agent 仍是 Answer（tool loop）。复杂度放在可测 stage，不放在 agent 群聊。
