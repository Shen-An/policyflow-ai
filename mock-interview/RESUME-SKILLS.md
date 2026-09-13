# 简历 · 技能栏（定稿）

> 用途:简历「专业技能」板块。关键词块为主(过初筛用);密写版可选,放「技术能力」段。
> 文末「追问口径」与 SELF-INTRO.md 对齐——被追问时按这个口径讲,别超简历没写的。

## 技术栈(关键词块 · 简历主用)

**核心方向**:RAG、AI Agent 应用开发、Prompt Engineering;开发语言:Python、Java、JavaScript、SQL

**RAG 检索**:Hybrid 双路检索(RAG + BM25、RRF 融合)、cross-encoder 重排、Embedding 向量化、语义切片、多格式文档解析、CRUD-RAG 基准评测

**Agent 与大模型**:Multi-Agent 中心化编排、LangGraph、Graph Agent、Agent Loop、Harness(Agent = Model + Harness)、意图分诊、Tool Calling、多层记忆、查询改写、健壮性兜底;SSE 流式、Prompt Caching、语音集成

**后端与工程**:FastAPI、Spring Boot、Spring AI、SQLModel + SQLAlchemy、MyBatis、SQLite / PostgreSQL / MySQL、Vue 3 + Element Plus、向量数据库 / 知识库构建、Electron、Git、Codex、Claude Code

## 技术能力(密写版 · 可选)

- **精通 RAG 系统开发**:实现 Hybrid 双路检索(Dense 向量召回 + BM25,RRF 融合),Rerank 重排,支持语义切片、多格式文档解析与知识库构建;基于 CRUD-RAG 基准完成检索评测(Hit@1/5/10、MRR),按检索策略量化对比。
- **熟练 Tool Calling 机制**:自定义并分类业务工具,实现意图分诊 / 路由 + 工具调用循环(tool loop),完成工具编排与规则校验。
- **掌握 MCP 模型上下文协议开发**:以 stdio / HTTP 真协议标准化封装数据源与工具,企业连接器可 mock 接入,降低集成成本。
- **熟练 Multi-Agent 中心化编排**:Orchestrator 串联「意图分诊 → 检索 → Answer Agent → 记忆回写」,实现任务流转与状态管理。
- **熟悉多层记忆 + 查询改写**:设计四层记忆(消息 / 近窗+滚动摘要 / 事件向量摘要 / 实体)与冷热 prompt 装配;短跟进句结合历史做 Query Rewrite,避免多轮检索丢主题。
- **熟悉 Agent 健壮性与安全兜底**:无可靠证据 hard refuse,跑题门控(off-topic / near-miss 分级拦截),insufficient_evidence 拒绝编造清单,高风险二次确认。
- **熟练 FastAPI 后端**:SSE 流式(记忆/改写/检索/回答/回写分阶段)、Prompt Caching、鉴权 / 中间件 / 异常处理;SQLModel + SQLAlchemy,SQLite(可迁移 PostgreSQL)。
- **擅长智能体调试优化**:依托分阶段 SSE trace、节点日志与检索评测指标,快速定位流程、检索与模型幻觉问题。
- **熟练 Skills 模块化技能体系**:Skill = 证据绑定业务规程,模块化封装、按需触发,无证据即 insufficient_evidence。

## 追问口径(面试用,简历不写;与 SELF-INTRO.md 一致)

- **cross-encoder 重排**:项目默认本地 lexical fusion,cross-encoder 为 opt-in 开关(失败 503 不静默回退)→ 讲「开关对照」,不说「默认在线」。
- **Multi-Agent**:口径是「中心化 Supervisor 流水线 + tool-using 主 agent,检索是服务,不是群聊框架」(docs/08 口径)。
- **LangGraph / Graph Agent**:落地以自研中心化编排为主;被问到时讲清 state / 节点如何管、与自研编排的取舍。
- **Codex / Claude Code**:辅助开发工具,关键设计边界自定(真实现 / 简化实现文档写明)。
