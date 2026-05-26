# 01. 总体架构详细设计

版本：v0.1  
日期：2026-07-09  
项目：Enterprise Policy Assistant / 企业内部制度问答与流程助手  
技术基线：FastAPI + SQLite + LightRAG + Skill 编排

---

## 1. 设计目标

本项目定位为一个适合实习项目和面试讲解的轻量企业 AI Agent 应用，不做通用聊天机器人，也不做完整 OA / 工单 / 审批系统。系统第一阶段聚焦：

1. 企业制度文档管理；
2. 基于 LightRAG 的可信问答；
3. 引用溯源；
4. 流程清单生成；
5. 申请 / 邮件 / 求助草稿生成；
6. FAQ 草稿生成与人工审核；
7. 轻量多 Agent Pipeline；
8. Skill / Tool Registry；
9. MCP mock 扩展；
10. 权限、审计、上下文防腐化和 RAG 评估。

MVP 必须坚持“小而完整”：业务闭环要完整，但不要扩展成复杂低代码平台、审批系统或工单系统。

---

## 2. 总体架构

系统采用分层架构：

```text
Frontend / API Client
        ↓
FastAPI Router Layer
        ↓
Application Service Layer
        ↓
Agent Pipeline Layer
        ↓
Skill / Tool Orchestration Layer
        ↓
RAG / LLM / MCP Infrastructure Layer
        ↓
SQLite + File Storage + LightRAG Workspaces
```

各层职责：

| 层级 | 职责 |
|---|---|
| API 层 | 接收 HTTP 请求、参数校验、认证依赖注入、返回统一响应 |
| Service 层 | 业务用例编排，如知识库管理、文档上传、聊天、草稿、FAQ 审核 |
| Agent Pipeline 层 | 问题路由、检索、回答、Skill 选择、合规检查、记忆更新 |
| Skill 层 | 业务能力封装，如流程清单、申请草稿、制度对比、FAQ 生成 |
| Tool 层 | 底层可执行动作，如知识检索、草稿保存、FAQ 入库、MCP 调用 |
| RAG / LLM 层 | LightRAG 封装、模型调用适配、embedding 接入 |
| 存储层 | SQLite 业务数据、uploads 原始文件、rag_workspaces 索引文件 |

---

## 3. 后端目录结构

推荐目录：

```text
backend/
  app/
    main.py

    core/
      config.py
      security.py
      logging.py
      permissions.py
      exceptions.py

    db/
      session.py
      base.py
      models.py
      init_db.py
      migrations/

    api/
      deps.py
      routes_auth.py
      routes_users.py
      routes_kb.py
      routes_chat.py
      routes_draft.py
      routes_faq.py
      routes_skill.py
      routes_tool.py
      routes_mcp.py
      routes_audit.py
      routes_eval.py

    schemas/
      auth.py
      user.py
      knowledge.py
      chat.py
      draft.py
      faq.py
      skill.py
      tool.py
      mcp.py
      audit.py
      eval.py

    services/
      auth_service.py
      user_service.py
      permission_service.py
      knowledge_base_service.py
      document_service.py
      rag_service.py
      llm_service.py
      chat_service.py
      draft_service.py
      faq_service.py
      memory_service.py
      context_service.py
      audit_service.py
      eval_service.py

    agents/
      base.py
      router_agent.py
      retrieval_agent.py
      answer_agent.py
      skill_agent.py
      compliance_agent.py
      memory_agent.py
      pipeline.py

    skills/
      base.py
      registry.py
      specs/
      handlers/

    tools/
      base.py
      registry.py
      knowledge_tools.py
      draft_tools.py
      faq_tools.py
      memory_tools.py
      mcp_tools.py

    mcp/
      client.py
      manager.py
      mock_server.py
      mock_tools.py

    rag/
      lightrag_adapter.py
      bm25_retriever.py
      hybrid_retriever.py
      rerank_service.py
      document_loader.py
      chunk_parser.py

    evals/
      rag_eval_dataset.py
      retrieval_metrics.py
      ragas_runner.py
      eval_runner.py

    tests/

frontend/
rag_workspaces/
uploads/
docs/
```

---

## 4. 核心运行流程

### 4.1 制度问答主流程

```text
User Question
  ↓
POST /api/chat
  ↓
ChatService.ask()
  ↓
RouterAgent.route()
  ↓
PermissionService.filter_readable_kbs()
  ↓
RetrievalAgent.retrieve()
  ↓
LightRAGService.query()
  ↓
AnswerAgent.answer_with_citations()
  ↓
SkillAgent.select_optional_skill()
  ↓
ComplianceAgent.check()
  ↓
Message / ai_query_logs / tool_call_logs / audit_logs
  ↓
Final Response
```

设计原则：

1. 制度类问题每轮必须重新检索；
2. 回答必须绑定本轮 evidence；
3. 无证据不得编造；
4. 权限过滤必须发生在检索前；
5. Agent 不直接访问数据库或 LightRAG，必须通过 Service / Tool；
6. 所有 Tool 调用必须记录日志。

---

## 5. 主要模块说明

### 5.1 Auth / User / Permission

负责：

- 用户登录；
- JWT 签发；
- 用户角色；
- 部门归属；
- 知识库 ACL；
- API 权限依赖；
- 工具调用权限检查。

MVP 角色：

```text
employee
kb_admin
sys_admin
```

### 5.2 Knowledge Base

负责：

- 知识库 CRUD；
- 文档上传；
- 文本抽取；
- 文档 hash；
- 索引任务；
- LightRAG workspace 管理。

MVP 支持文件：

```text
txt
md
docx
pdf 文本型
```

扫描 PDF OCR 暂不做。

### 5.3 RAG Service

RAG 层不只封装 LightRAG，还要预留传统关键词检索、混合检索、重排序和评估扩展能力。MVP 阶段以 `LightRAGService` 为主，但接口设计必须避免业务代码直接绑定 LightRAG。

核心组件：

| 组件 | 职责 | MVP 状态 |
|---|---|---|
| `LightRAGService` | LightRAG workspace 管理、插入、查询、引用转换 | 必做 |
| `BM25Retriever` | 基于文档 chunk 的关键词检索，后续可接 rank-bm25 / SQLite FTS / Elasticsearch | 预留接口，建议实现轻量版 |
| `HybridRetriever` | 融合 LightRAG 与 BM25 结果，统一排序与去重 | 预留，阶段二/三实现 |
| `RerankService` | 对候选证据做 rerank，可接 bge-reranker、cross-encoder 或 LLM rerank | 预留接口，默认关闭 |
| `RetrievalEvaluator` | 计算 Hit@K、MRR、Recall@K 等检索指标 | 预留并在评估阶段实现 |
| `RagasRunner` | 调用 RAGAS 评估 faithfulness、answer_relevancy、context_precision 等 | 预留，可选实现 |

`LightRAGService` 职责：

- 创建 / 定位 workspace；
- 插入文档；
- 增量索引；
- 查询；
- 删除或重建；
- 统一引用格式；
- 错误转换。

RAG 检索链路预留为：

```text
RetrievalAgent
  ↓
Permission-filtered KB scope
  ↓
LightRAG candidates       BM25 candidates
        \                 /
         \               /
          HybridRetriever merge + deduplicate
                    ↓
            optional RerankService
                    ↓
              top_k evidence
                    ↓
              AnswerAgent
```

MVP 默认策略：

```text
retrieval_strategy = lightrag_only
rerank_enabled = false
```

后续扩展策略：

```text
retrieval_strategy = hybrid_lightrag_bm25
rerank_enabled = true
```

注意：`AnswerAgent`、`SkillAgent`、`ComplianceAgent` 只能依赖统一 `Evidence` 结构，不能感知证据来自 LightRAG、BM25 还是 rerank。

### 5.4 LLM Service

负责模型调用适配。

MVP 推荐先做 OpenAI-compatible：

```text
DeepSeek / Qwen / GLM / 豆包 / OpenAI-compatible 服务
```

后续可以增加 Anthropic 原生 Provider。

抽象接口：

```python
class LLMService:
    async def chat(self, messages, model=None, temperature=None, tools=None): ...
    async def structured_chat(self, messages, schema, model=None): ...
    async def embed(self, texts): ...
```

注意：模型配置不得写死，应放入环境变量和 `model_providers` 表。

### 5.5 Agent Pipeline

采用确定性 Pipeline，而不是无限循环自治 Agent。

核心节点：

```text
RouterAgent
RetrievalAgent
AnswerAgent
SkillAgent
ComplianceAgent
MemoryAgent
```

每个 Agent 都应该具备明确输入和输出 schema。

### 5.6 Skill System

Skill 是业务能力封装，不是工具。

内置 Skill：

```text
knowledge_qa
process_checklist
application_draft
policy_compare
faq_generate
risk_check
summary
```

### 5.7 Tool Registry

Tool 是底层可执行动作。所有 Tool 调用都通过 `ToolRegistry`，并写入 `tool_call_logs`。

MVP Tool：

```text
knowledge.search
knowledge.insert
knowledge.reindex
draft.create
draft.update
faq.create_draft
faq.approve
memory.read
memory.write
mcp.call
```

### 5.8 MCP mock

MVP 中 MCP 不连接真实外部系统，只做 mock server / mock client。

mock 工具：

```text
mcp.email.create_draft
mcp.calendar.create_event
mcp.feishu.send_message_mock
mcp.confluence.search_mock
```

---

## 6. 数据与文件存储

```text
SQLite:
  用户、角色、知识库元数据、文档状态、会话、消息、草稿、日志、配置

uploads/:
  原始上传文件

rag_workspaces/:
  LightRAG 索引文件

logs/:
  应用日志，可选
```

建议路径：

```text
backend/data/app.db
uploads/{knowledge_base_code}/{document_id}/original.xxx
rag_workspaces/{knowledge_base_code}/
```

---

## 7. 安全边界

MVP 必须实现：

1. 用户认证；
2. RBAC；
3. 知识库 ACL；
4. 检索前权限过滤；
5. 上传文件类型限制；
6. 上传文件大小限制；
7. Tool 权限检查；
8. MCP 默认关闭；
9. 审计日志普通用户不可删除；
10. API key 不入库，只保存环境变量名。

---

## 8. 上下文防腐化设计原则

关键原则：

1. 历史对话不能替代制度检索；
2. 用户偏好记忆不能覆盖知识库证据；
3. 草稿生成必须使用本轮证据；
4. FAQ 入库必须人工审核；
5. 会话摘要区分事实、证据、推测和待确认事项；
6. 知识库更新后旧回答不能作为事实复用。

### 8.1 四层记忆与 Context Window

每轮 Chat 的记忆闭环：

```text
persist user message
  → MemoryLoad（固定偏好/实体 + 最近 K 轮 + 向量按需召回）
  → build_context 分区装配
  → Router / Retrieval（本轮必检）/ Answer / Skill / Compliance
  → persist assistant
  → MemoryWriteback（事件抽取 → LTM/Entity/偏好；超窗压缩卸载）
  → 清空请求级 working set
```

AnswerAgent 消费的 `MemoryWorkingSet` 仅作非权威上下文（指代、风格、任务状态）；制度事实仍只绑定本轮 Evidence。

---

## 9. 非功能目标

| 指标 | MVP 目标 |
|---|---|
| 并发用户 | 10-20 |
| 文档规模 | 500-1000 篇以内 |
| 普通问答响应 | 5-15 秒 |
| 索引方式 | FastAPI BackgroundTasks |
| 部署方式 | 本地 / Docker |
| 数据库 | SQLite |
| 日志 | 结构化日志 + 审计表 |

---

## 10. 后续扩展点

MVP 之后可扩展：

1. SQLite 迁移 PostgreSQL；
2. BackgroundTasks 迁移 Celery + Redis；
3. mock MCP 替换真实飞书 / 邮箱 / Jira / Confluence；
4. LightRAG 存储迁移 PostgreSQL / Qdrant / Neo4j；
5. BM25 从轻量本地实现迁移到 SQLite FTS5 / Elasticsearch / OpenSearch；
6. 增加 RerankService，支持 bge-reranker、cross-encoder 或 LLM rerank；
7. 增加 Hit@K、MRR、Recall@K 和 RAGAS 自动评估流水线；
8. 增加企业 SSO；
9. 增加前端管理后台；
10. 增加 RAG 自动评估报告。
