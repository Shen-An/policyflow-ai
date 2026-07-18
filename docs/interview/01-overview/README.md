# 01. 项目定位与电梯稿

## 一句话

PolicyFlow AI = **企业内部政策问答与流程助手**，可面试演示的 **tool-using RAG**（诚实 Skill / Tool / MCP + 可量化检索评测），不是假 multi-agent 壳。

## 30 秒电梯稿（建议背）

> 我做的是企业制度问答系统。主路径是统一编排的 tool-using RAG：Router 结构化路由 → Hybrid 检索 → 可选 Skill 规程 → Answer 工具环 → 质量门 → 多层记忆回写。  
> Skill / Tool / MCP 分层诚实实现；评估用 CRUD 金标看 Hit@K / MRR，并写清检索策略与样本量。  
> 记忆只做指代与个性化，不能覆盖本轮制度证据。

## 技术栈（如实）

| 层 | 选型 |
|---|---|
| Backend | FastAPI + SQLModel + SQLite |
| RAG | Hybrid（LightRAG 路径 + BM25）+ 本地 lexical fusion rerank |
| AI 编排 | `AgentPipeline` 单编排；Answer 为主 agent |
| Frontend | React + 管理/聊天/评估页；Chat SSE |
| 环境 | conda `policyflow`，Python 3.11+ |

## 面试官常问：你解决什么问题？

1. **制度问答要可追溯**：回答绑定本轮检索证据，硬拒答无可靠证据  
2. **流程题要结构化**：Skill 在证据上出清单/对比，无证据不编  
3. **多轮不断线**：记忆 + query rewrite 处理「给我模板」类短跟进  
4. **效果可量化**：专用评测库 + Hit@K/MRR，避免业务库灌金标虚高  

## 简历可写 / 不该写

| 可写 | 不该写 |
|---|---|
| tool-using RAG / 统一编排 | multi-agent 平台 / 自主多智能体协作 |
| Hybrid 检索 + Hit@K/MRR | 生产级向量库、百万级召回 |
| 诚实 MCP 客户端 + mock 企业连接器 | 已对接飞书/企微生产 |
| 四层记忆与上下文装配 | 完整 Memory OS / 物理冷热分层存储 |
| 本地 lexical rerank | BGE / cross-encoder 已上线 |

## 关键入口

- 策略总文档：`docs/08-de-toy-multiagent-skill-eval-strategy.md`
- 架构：`docs/01-architecture-design.md`
- 演示脚本：`docs/09-interview-demo-script.md`
- 代码入口：`backend/app/main.py`、`backend/app/agents/pipeline.py`
