# PolicyFlow AI 面试知识库

版本：v1.0  
日期：2026-07-18  
用途：把本项目**真实可讲、可指代码**的面试点集中整理；优先诚实边界，不写简历注水话术。

> 配套：
> - 设计总策略：[`../08-de-toy-multiagent-skill-eval-strategy.md`](../08-de-toy-multiagent-skill-eval-strategy.md)
> - 现场演示脚本：[`../09-interview-demo-script.md`](../09-interview-demo-script.md)

---

## 怎么用

| 场景 | 打开 |
|---|---|
| 30 秒自我介绍 | [01-overview](01-overview/README.md) |
| 被问「为什么不是玩具 multi-agent」 | [02-architecture](02-architecture/README.md) + [04](04-agent-skill-tool-mcp/README.md) |
| 被问 RAG / Hybrid / Rerank | [03-rag-retrieval](03-rag-retrieval/README.md) |
| 被问记忆 / 多轮 / 上下文窗口 | [05-memory-context](05-memory-context/README.md) |
| 被问评估指标 / Hit@K / MRR | [06-eval-metrics](06-eval-metrics/README.md) |
| 被问后端工程 / FastAPI / SQLite | [07-backend-engineering](07-backend-engineering/README.md) |
| 被问前端 / SSE / 体验 | [08-frontend-ux](08-frontend-ux/README.md) |
| 被追问边界 / mock / 没做啥 | [09-honesty-boundaries](09-honesty-boundaries/README.md) |
| 临场 demo + 高频 Q&A | [10-demo-qa](10-demo-qa/README.md) |

**原则：能指到代码或测试的才写；说不清就标「半实现 / 不做」。**

---

## 目录结构

```text
docs/interview/
├── README.md                          # 本文件：总目录
├── 01-overview/                       # 项目定位与电梯稿
├── 02-architecture/                   # 整体架构与编排诚实性
├── 03-rag-retrieval/                  # 检索、Hybrid、Rerank
├── 04-agent-skill-tool-mcp/           # Agent / Skill / Tool / MCP
├── 05-memory-context/                 # 四层记忆与上下文管理
├── 06-eval-metrics/                   # CRUD Eval 与指标
├── 07-backend-engineering/            # FastAPI / SQLModel / 工程点
├── 08-frontend-ux/                    # 聊天 UX / SSE / 管理面
├── 09-honesty-boundaries/             # 必说边界与反吹牛清单
└── 10-demo-qa/                        # 演示路径与高频追问
```

---

## 推荐 15 分钟叙事线

1. **定位（1 min）**：企业政策问答；tool-using RAG，不是假 multi-agent 壳  
2. **主路径（3 min）**：Router → Hybrid 检索 → Skill? → Answer tool loop → Verifier → Memory writeback  
3. **诚实分层（3 min）**：Skill ≠ Tool ≠ MCP；Retrieval 是 Service  
4. **记忆（2 min）**：滑动窗口 + 摘要 + 结构化抽取 + 重要性/时间排序；冷热=装配  
5. **评估（3 min）**：Hit@1/5/10 + MRR，写清策略与 N；eval_test 专用库 + 干扰文档  
6. **边界（2 min）**：本地 rerank、mock MCP、记忆非权威、SQLite 规模边界  
7. **现场点一点代码 / diagnostics（1 min）**

---

## 一页「项目卖点 vs 边界」

| 可讲卖点 | 必须同步说的边界 |
|---|---|
| 统一 `AgentPipeline`，Chat/Eval 同编排 | 不是多独立 Agent 平台 |
| Hybrid 检索 + 可量化 Hit@K/MRR | Hybrid 在 1-doc 任务上未必显著优于 BM25 |
| Skill 证据绑定，无证据 `insufficient_evidence` | 禁止假 `skill.suggest` diagnostics |
| MCP 真协议客户端 | 企业连接器可 mock，响应带 `status=mock` |
| 四层记忆 + query rewrite | 记忆非权威；偏好禁政策事实；冷热非物理冷存 |
| SSE 阶段可视化 | 前端体验持续打磨，不是生产级 IM |

---

## 维护约定

- 改 AI 表面行为（检索 / Skill / 记忆 / Eval）时：同步更新对应章节 + `docs/08` 落地状态  
- 新增面试点：只写「代码里有的」；假设/规划放「未做」小节  
- 面试前：跑一遍 [`../09-interview-demo-script.md`](../09-interview-demo-script.md) 自检清单  
