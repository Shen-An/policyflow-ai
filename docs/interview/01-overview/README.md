# 01. 项目定位与电梯稿

## 一句话

PolicyFlow AI = **企业内部政策问答与流程助手**，可面试演示的 **tool-using RAG**（诚实 Skill / Tool / MCP + 可量化检索评测），不是假 multi-agent 壳。

## 30 秒电梯稿（建议背）

> 我做的是企业制度问答系统。主路径是统一编排的 tool-using RAG：Router 结构化路由 → Hybrid 检索 → 可选 Skill 规程 → Answer 工具环 → 质量门 → 多层记忆回写。  
> Skill / Tool / MCP 分层诚实实现；评估用 CRUD 金标看 Hit@K / MRR，并写清检索策略与样本量。  
> 记忆只做指代与个性化，不能覆盖本轮制度证据。

### 白话版（背不下技术版时用这个）

> 我做的是个**企业内部答政策问的系统**。员工问年假、报销、采购这些制度怎么走,系统先去公司知识库翻相关条款,**照着翻到的内容答**,并在答案上标出处(`[1][2]`,点开能看到原文)——不是大模型凭记忆瞎编。**翻不到可靠依据就老实说答不了。**
>
> 流程类问题(比如出差报销流程)会把翻到的条款**整理成清单或对比表**。多轮聊天能**记住上文**(比如「给我模板」知道指的是刚才聊的那个)。
>
> 为了证明检索真有用,我用了**公开的中文问答数据集当标准答案**,跑出 **Hit@K 和 MRR** 这种指标,每次都说清用哪种检索、测了多少条——不报虚高的 100%。

**技术版 ↔ 白话版对照(被问到哪个词,这么翻译):**

| 技术版黑话 | 白话 |
|---|---|
| tool-using RAG | 会用工具的「先查资料再答」系统 |
| Router 结构化路由 | 先分诊:判断这问题归哪个库、什么类型、难不难 |
| Hybrid 检索 | 两路一起找:按意思找 + 按字面找,再合并 |
| Skill 规程 | 照着证据整理成清单/对比表的业务规程 |
| Answer 工具环 | 真正会动脑的那个,一边想一边调工具 |
| 质量门 | 答案出门前的检查:跑题/没依据就打回或拒答 |
| 多层记忆回写 | 答完后把本轮值得记的存进记忆 |
| Hit@K / MRR | 正确资料排得多靠前;每次报必带策略和样本量 |

---

## 技术栈（如实）

| 层 | 选型 |
|---|---|
| Backend | FastAPI + SQLModel + SQLite |
| RAG | Hybrid（LightRAG 路径 + BM25，超时降级 BM25 并打标）+ 可选 rerank：默认本地 lexical fusion，可选真实 NVIDIA cross-encoder（opt-in） |
| AI 编排 | `AgentPipeline` 单编排；Answer 为主 agent；请求级 Turn Budget + 检索质量门 + 答案发布门 |
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
| 默认本地 lexical rerank + 可选真实 NVIDIA cross-encoder（opt-in、可评测对比） | 自研 BGE / 默认在线 cross-encoder / 生产级低延迟重排 |
| 请求级 Turn Budget + 质量门 + 答案发布门（基础版兜底） | 分布式 Saga / 熔断 / 自愈平台 |
| 两套隔离评测库（CRUD 主指标 + 自建企业政策 sanity 集） | 严格 benchmark / 大规模第三方评测 |

## 关键入口

- 策略总文档：`docs/08-de-toy-multiagent-skill-eval-strategy.md`
- 架构：`docs/01-architecture-design.md`
- 演示脚本：`docs/09-interview-demo-script.md`
- 代码入口：`backend/app/main.py`、`backend/app/agents/pipeline.py`
