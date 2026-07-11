# 05. 开发路线图与里程碑

版本：v0.1  
日期：2026-07-10  
项目：Enterprise Policy Assistant  
技术基线：FastAPI + SQLite + LightRAG + Skill 编排

---

## 1. 总体阶段划分

```
Phase 0 — 项目骨架        （Day 1-2）
Phase 1 — 核心基础设施     （Day 3-5）
Phase 2 — RAG 与问答管线   （Day 6-10）
Phase 3 — Agent、Skill 与草稿（Day 11-14）
Phase 4 — FAQ 与评估体系   （Day 15-17）
Phase 5 — 验收、优化与扩展  （Day 18-20）
```

---

## 2. Phase 0：项目骨架

**目标**：搭建项目基础结构，确保可运行、可调试。

### 2.1 任务清单

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 0.1 | 初始化 Python 项目结构（pyproject.toml、依赖管理） | `pyproject.toml`、`requirements.txt` | 0.5d |
| 0.2 | 创建 `backend/app/` 目录骨架 | 空模块文件 + `__init__.py` | 0.5d |
| 0.3 | 实现 `config.py`（环境变量、配置加载） | `backend/app/core/config.py` | 0.5d |
| 0.4 | 实现 `exceptions.py`（统一异常体系） | `backend/app/core/exceptions.py` | 0.5d |
| 0.5 | 实现 `logging.py`（结构化日志） | `backend/app/core/logging.py` | 0.5d |
| 0.6 | FastAPI 入口 `main.py` + 健康检查 | `backend/app/main.py` | 0.5d |
| 0.7 | 创建数据库会话、Base、初始化脚本 | `backend/app/db/session.py`、`base.py` | 1d |
| 0.8 | 初始化数据脚本（角色、部门、默认知识库） | `backend/app/db/init_db.py` | 0.5d |

### 2.2 交付物

- 可启动的 FastAPI 应用
- 配置加载机制
- 统一异常处理
- 结构化日志
- SQLite 数据库创建 + 初始化种子数据

### 2.3 验收标准

```text
1. uvicorn 启动成功，无 import 错误
2. GET /health 返回 200
3. 数据库文件创建成功，种子数据（3 角色、5 部门、5 知识库）写入
4. 日志文件正常工作
5. 配置可通过环境变量覆盖
```

---

## 3. Phase 1：核心基础设施

**目标**：实现用户认证、权限管理、知识库 CRUD、文档上传与文本抽取。

### 3.1 用户与认证

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 1.1 | 创建 `users`、`roles`、`user_roles`、`departments` 模型 | `backend/app/db/models.py`（部分） | 0.5d |
| 1.2 | 实现 `security.py`（JWT 签发与验证） | `backend/app/core/security.py` | 0.5d |
| 1.3 | 实现 `deps.py`（认证依赖注入、当前用户获取） | `backend/app/api/deps.py` | 0.5d |
| 1.4 | 实现 `permissions.py`（角色校验、权限检查） | `backend/app/core/permissions.py` | 0.5d |
| 1.5 | 实现登录 API、当前用户 API | `backend/app/api/routes_auth.py` | 0.5d |
| 1.6 | 实现用户管理 API | `backend/app/api/routes_users.py` | 0.5d |
| 1.7 | 创建 auth/user schema | `backend/app/schemas/auth.py`、`user.py` | 0.5d |

### 3.2 知识库与文档

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 1.8 | 创建 `knowledge_bases`、`knowledge_base_permissions`、`knowledge_documents`、`rag_index_jobs` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 1.9 | 实现知识库 CRUD service | `backend/app/services/knowledge_base_service.py` | 0.5d |
| 1.10 | 实现知识库 API | `backend/app/api/routes_kb.py` | 0.5d |
| 1.11 | 实现文档上传 service（文件保存 + 文本抽取） | `backend/app/services/document_service.py` | 1d |
| 1.12 | 实现 `document_loader.py`（txt/md/docx/pdf 解析） | `backend/app/rag/document_loader.py` | 1d |
| 1.13 | 实现 `permission_service.py`（知识库 ACL 查询） | `backend/app/services/permission_service.py` | 0.5d |
| 1.14 | 创建 KB/document schema | `backend/app/schemas/knowledge.py` | 0.5d |
| 1.15 | 审计日志模型 + service | `backend/app/services/audit_service.py` | 0.5d |

### 3.3 交付物

- 完整用户认证体系（登录、JWT、角色）
- 知识库 CRUD + ACL 权限过滤
- 文档上传、文件类型校验、文本抽取
- 审计日志记录

### 3.4 验收标准

```text
1. 用户可登录，返回 JWT
2. 不同角色访问受限接口时返回 PERMISSION_DENIED
3. 可创建/查询知识库
4. 上传 docx/pdf/txt/md 文件成功，抽取文本正确
5. 权限过滤：用户只能看到有 read 权限的知识库
6. 审计日志：上传文档、创建知识库等操作被记录
```

---

## 4. Phase 2：RAG 与问答管线

**目标**：接入 LightRAG，实现完整的制度问答链路。

### 4.1 LightRAG 集成

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 2.1 | 创建 `model_providers` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 2.2 | 实现 `llm_service.py`（OpenAI-compatible 调用） | `backend/app/services/llm_service.py` | 1d |
| 2.3 | 实现 `lightrag_adapter.py`（workspace 管理、插入、查询） | `backend/app/rag/lightrag_adapter.py` | 1.5d |
| 2.4 | 实现 `rag_service.py`（统一检索接口） | `backend/app/services/rag_service.py` | 1d |
| 2.5 | 创建 retrieval schema（Evidence、RetrievalRequest） | `backend/app/schemas/retrieval.py` | 0.5d |
| 2.6 | 索引任务触发（文档上传后自动索引） | `backend/app/services/document_service.py`（补充） | 0.5d |

### 4.2 Agent Pipeline

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 2.7 | 实现 Agent 基类 | `backend/app/agents/base.py` | 0.5d |
| 2.8 | 实现 `router_agent.py`（问题路由） | `backend/app/agents/router_agent.py` | 0.5d |
| 2.9 | 实现 `retrieval_agent.py`（检索调度） | `backend/app/agents/retrieval_agent.py` | 0.5d |
| 2.10 | 实现 `answer_agent.py`（证据绑定回答） | `backend/app/agents/answer_agent.py` | 1d |
| 2.11 | 实现 `compliance_agent.py`（合规检查） | `backend/app/agents/compliance_agent.py` | 0.5d |
| 2.12 | 实现 `pipeline.py`（Pipeline 编排） | `backend/app/agents/pipeline.py` | 0.5d |

### 4.3 Chat API

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 2.13 | 创建 `conversations`、`messages`、`ai_query_logs` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 2.14 | 实现 `chat_service.py` | `backend/app/services/chat_service.py` | 1d |
| 2.15 | 实现聊天 API + 会话查询 | `backend/app/api/routes_chat.py` | 0.5d |
| 2.16 | 创建 chat schema | `backend/app/schemas/chat.py` | 0.5d |

### 4.4 交付物

- LightRAG 文档索引与查询
- 完整 QA Pipeline（路由 → 检索 → 回答 → 合规）
- 聊天 API（发送问题、创建会话、返回引用）
- 无证据拒答

### 4.5 验收标准

```text
1. 上传文档后自动触发索引，索引状态正确流转
2. 制度问题返回带引用的回答
3. 无相关证据时返回拒答，不编造
4. citations 包含 document_title、snippet、score
5. 权限过滤在检索前生效
6. ai_query_logs 记录检索 trace
```

---

## 5. Phase 3：Agent、Skill 与草稿

**目标**：实现 Skill 编排、Tool 调用、草稿生成能力。

### 5.1 Skill 系统

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 3.1 | 创建 `skills`、`tools`、`tool_call_logs` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 3.2 | 实现 Skill 基类 + Registry | `backend/app/skills/base.py`、`registry.py` | 1d |
| 3.3 | 实现 Tool 基类 + Registry | `backend/app/tools/base.py`、`registry.py` | 1d |
| 3.4 | 实现内置 Skill（process_checklist、policy_compare、summary） | `backend/app/skills/handlers/` | 1d |
| 3.5 | 实现内置 Tool（knowledge.search、draft.create 等） | `backend/app/tools/` | 1d |
| 3.6 | 实现 `skill_agent.py`（Skill 选择与调用） | `backend/app/agents/skill_agent.py` | 0.5d |
| 3.7 | 实现 Skill/Tool API | `backend/app/api/routes_skill.py`、`routes_tool.py` | 0.5d |
| 3.8 | 创建 skill/tool schema | `backend/app/schemas/skill.py`、`tool.py` | 0.5d |

### 5.2 草稿系统

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 3.9 | 创建 `drafts` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 3.10 | 实现 `draft_service.py` | `backend/app/services/draft_service.py` | 0.5d |
| 3.11 | 实现草稿 API（CRUD + 确认 + 导出） | `backend/app/api/routes_draft.py` | 0.5d |
| 3.12 | 创建 draft schema | `backend/app/schemas/draft.py` | 0.5d |

### 5.3 MCP Mock

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 3.13 | 实现 MCP mock server + mock tools | `backend/app/mcp/mock_server.py`、`mock_tools.py` | 1d |
| 3.14 | 实现 MCP Manager | `backend/app/mcp/manager.py` | 0.5d |
| 3.15 | 创建 `mcp_servers` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 3.16 | 实现 MCP API | `backend/app/api/routes_mcp.py` | 0.5d |

### 5.4 记忆与上下文

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 3.17 | 创建 `memory_items` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 3.18 | 实现 `memory_service.py` | `backend/app/services/memory_service.py` | 0.5d |
| 3.19 | 实现 `context_service.py`（上下文防腐化） | `backend/app/services/context_service.py` | 0.5d |
| 3.20 | 实现 `memory_agent.py` | `backend/app/agents/memory_agent.py` | 0.5d |

### 5.5 交付物

- Skill 注册、选择与执行链路
- 草稿生成与管理
- MCP mock 扩展点
- 记忆与上下文管理

### 5.6 验收标准

```text
1. 问答后可自动推荐 Skill（如差旅问题推荐 process_checklist）
2. 草稿可创建、编辑、确认、导出
3. Tool 调用记录写入 tool_call_logs
4. MCP mock 服务可注册、健康检查
5. 记忆不覆盖知识库证据
6. 上下文防腐化：历史对话不替代制度检索
```

---

## 6. Phase 4：FAQ 与评估体系

**目标**：实现 FAQ 生成与审核流程、RAG 评估指标计算。

### 6.1 FAQ

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 4.1 | 创建 `faq_drafts` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 4.2 | 实现 `faq_service.py`（FAQ 生成 + 审核） | `backend/app/services/faq_service.py` | 1d |
| 4.3 | 实现 FAQ API（生成草稿、审核、驳回） | `backend/app/api/routes_faq.py` | 0.5d |
| 4.4 | 创建 faq schema | `backend/app/schemas/faq.py` | 0.5d |

### 6.2 评估体系

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 4.5 | 创建 `eval_cases`、`retrieval_eval_items`、`eval_runs`、`eval_results` 模型 | `backend/app/db/models.py`（补充） | 0.5d |
| 4.6 | 实现 `retrieval_metrics.py`（Hit@K、MRR、Recall@K） | `backend/app/evals/retrieval_metrics.py` | 0.5d |
| 4.7 | 实现 `eval_runner.py`（评估编排） | `backend/app/evals/eval_runner.py` | 1d |
| 4.8 | 实现 `eval_service.py` | `backend/app/services/eval_service.py` | 0.5d |
| 4.9 | 实现 `ragas_runner.py`（RAGAS 可选集成） | `backend/app/evals/ragas_runner.py` | 1d |
| 4.10 | 实现评估 API（用例管理 + 运行 + 结果查询） | `backend/app/api/routes_eval.py` | 1d |
| 4.11 | 创建 eval schema | `backend/app/schemas/eval.py` | 0.5d |

### 6.3 交付物

- FAQ 自动生成 + 人工审核流程
- 检索指标计算（Hit@K、MRR、Recall@K）
- Eval Runner 可批量运行测试用例
- RAGAS 可选集成（MVP 默认关闭）

### 6.4 验收标准

```text
1. FAQ 草稿可生成、查询、审核通过、驳回
2. 审核通过的 FAQ 触发 LightRAG 增量索引
3. Hit@K / MRR / Recall@K 计算结果正确
4. eval run 保存完整配置快照
5. RAGAS disabled 时返回 skipped，不写 0 分
6. retrieval-debug 接口返回完整 trace
```

---

## 7. Phase 5：验收、优化与扩展

**目标**：端到端验收、补充测试、性能优化、文档完善。

### 7.1 测试

| # | 任务 | 输出文件 | 预估 |
|---|---|---|---|
| 5.1 | Retrieval Metrics 单元测试 | `backend/app/tests/test_retrieval_metrics.py` | 0.5d |
| 5.2 | RAGService 单元测试 | `backend/app/tests/test_rag_service.py` | 0.5d |
| 5.3 | LightRAG Adapter 测试 | `backend/app/tests/test_lightrag_adapter.py` | 0.5d |
| 5.4 | Eval Runner 测试 | `backend/app/tests/test_eval_runner.py` | 0.5d |
| 5.5 | API 集成测试 | `backend/app/tests/test_api.py` | 1d |
| 5.6 | Agent Pipeline 集成测试 | `backend/app/tests/test_pipeline.py` | 0.5d |

### 7.2 优化

| # | 任务 | 预估 |
|---|---|---|
| 5.7 | 响应时间优化（5-15 秒内满足 MVP 目标） | 0.5d |
| 5.8 | 错误处理完善、边界情况覆盖 | 0.5d |
| 5.9 | 统一的 request_id 链路追踪 | 0.5d |

### 7.3 验收对照

对照 `04-ai-pipeline-rag-eval-design.md` 第 17 节 MVP 验收标准逐条检查：

```text
1. ✅ 默认问答路径通过 LightRAG 检索
2. ✅ BM25 和 Hybrid strategy 已在 schema/API 中预留
3. ✅ 未实现的 BM25/Hybrid 不会静默返回空结果
4. ✅ RerankService 默认关闭
5. ✅ 未配置 reranker 时开启 rerank 会显式失败
6. ✅ Evidence 结构统一，AnswerAgent 不感知检索来源
7. ✅ retrieval_strategy 与 LightRAG query_mode 独立
8. ✅ ai_query_logs.retrieved_sources 记录检索 trace
9. ✅ retrieval eval 可计算 Hit@K、MRR、Recall@K
10. ✅ RAGAS hook 可选，disabled 时返回 skipped
11. ✅ retrieval-debug 可观察完整 trace
12. ✅ 所有未来扩展点有明确的文件与接口位置
```

### 7.4 后续扩展（MVP 之后）

```text
1. SQLite → PostgreSQL 迁移
2. BackgroundTasks → Celery + Redis
3. mock MCP → 真实飞书/邮箱/Jira/Confluence
4. LightRAG 存储 → PostgreSQL / Qdrant / Neo4j
5. BM25 轻量实现 → SQLite FTS5 / Elasticsearch / OpenSearch
6. 增加 RerankService（bge-reranker / cross-encoder）
7. 企业 SSO
8. 前端管理后台
9. RAG 自动评估报告
```

---

## 8. 依赖关系

```text
Phase 0 ──── 所有阶段依赖
   │
Phase 1 ──── Phase 2 依赖（需要 auth + KB + permission）
   │
Phase 2 ──── Phase 3 依赖（需要 agent pipeline 和 rag service）
   │
   ├─── Phase 3（skill/draft/mcp/memory）
   │
   └─── Phase 4（faq + eval，依赖 Phase 2）
             │
       Phase 5 ──── 所有阶段完成后再验收
```

关键路径：Phase 0 → Phase 1 → Phase 2 → Phase 4（Eval）

Phase 3（Skill/草稿/MCP）与 Phase 2 后的其他阶段可并行开发。

---

## 9. 工作量估算

| 阶段 | 工作日 | 模块数 | 文件数 |
|---|---|---|---|
| Phase 0：项目骨架 | 2 | 6 | ~15 |
| Phase 1：核心基础设施 | 3 | 8 | ~25 |
| Phase 2：RAG 与问答管线 | 5 | 8 | ~20 |
| Phase 3：Agent、Skill 与草稿 | 4 | 12 | ~30 |
| Phase 4：FAQ 与评估体系 | 3 | 6 | ~15 |
| Phase 5：验收、优化与扩展 | 3 | 6 | ~10 |
| **合计** | **20** | **46** | **~115** |

实际耗时取决于：
- LightRAG 集成复杂度（首次接入可能需额外 1-2 天调试）
- 文档解析（pdf 文本抽取可能因文档质量需要调整）
- 评估与 RAGAS 集成（依赖 evaluator LLM 配置）