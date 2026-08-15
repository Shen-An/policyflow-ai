# 07. 后端工程面试点

## 栈与工程形状

- **FastAPI** 应用：`backend/app/main.py`  
- **SQLModel + SQLite**（可迁 PostgreSQL 的模型层意识）  
- 分层：`api/` · `services/` · `agents/` · `rag/` · `db/`  
- 配置：`pydantic-settings`（`backend/app/core/config.py`）  
- 日志：结构化日志 + 审计相关表/轨迹  

## 值得讲的工程点

### 1. 清晰的请求链路

Chat：鉴权 → 会话消息落库 → MemoryLoad → rewrite → Pipeline → 落库回答 → MemoryWriteback  
SSE：`POST /api/chat/stream` 推 stage（MemoryLoad / rewrite / 检索 / 回答 / writeback）

### 2. 权限与多租户意识（MVP 级）

- 知识库按部门/授权过滤  
- 记忆 API **self-only**  
- Tool `memory.write` 拒写他人 owner  

### 3. 失败与拒答策略

- 无可靠证据 hard refuse（可配置）  
- Skill `insufficient_evidence`  
- embedding/LLM 失败：记忆路径 best-effort，不拖垮主回答（视具体路径）  

#### 错误兜底下一步规划（尚未实施）

面试时可以按四类问题回答：

1. **调用次数失控**：在 Tool/Reflection 各自轮次限制之外，增加单轮总 LLM 调用数、总检索次数和总耗时预算。
2. **RAG 有结果但质量差**：增加检索质量门；最多一次回退原问题或替代策略，仍不可靠就 hard refuse。
3. **答案有引用或事实问题**：将 Compliance 收敛成 `PASS / REVISE / REFUSE` 发布门；改稿一次后必须复检。
4. **外部操作失败或超时**：区分本地事务和外部副作用。外部写操作使用幂等键、`unknown` 状态、执行结果核对和补偿，而不是声称数据库 rollback 能撤销邮件或飞书消息。

详细场景见 [`../11-scenario-questions`](../11-scenario-questions/README.md) Q11、Q12。

### 4. 后台任务意识

- 文档索引后台排队，避免导入接口阻塞  
- Eval 跑批与在线问答隔离在「测试库」约定上  

### 5. 可测性

- `tests/test_memory_system.py`、phase2/3/4/5 分阶段  
- Fake LLM / Fake embedding / 适配器注入（`create_app(..., lightrag_adapter=, llm_service=)`）  

## 数据模型面试点

| 表/实体 | 可讲点 |
|---|---|
| `conversations` / `messages` | L0 + summary 字段 |
| `memory_items` | type / embedding JSON / expires_at / meta_json |
| `knowledge_bases` / documents | 业务库 vs eval_test |
| query logs | 审计与 diagnostics 回放基础 |

详见 `docs/02-database-design-sqlite.md`。

## 配置里和 AI 相关的旋钮（知即可）

- `MEMORY_STM_WINDOW_TURNS`、`MEMORY_LTM_TOP_K`  
- `MEMORY_LTM_SALIENCE_THRESHOLD`、`MEMORY_RANK_DECAY_LAMBDA`  
- `CHAT_HARD_REFUSE_WITHOUT_EVIDENCE`、`CHAT_TOOL_MAX_ROUNDS`
- `CHAT_TURN_TIMEOUT_SECONDS`、`CHAT_TURN_MAX_LLM_CALLS`
- `CHAT_TURN_MAX_RETRIEVAL_ATTEMPTS`、`CHAT_TURN_MAX_TOOL_CALLS`
- `CHAT_ANSWER_REVISE_MAX_ROUNDS`

## 边界

- SQLite + JSON embedding：**demo 合适，不适合超大规模向量检索**
- 无独立向量库 / 无生产级队列（BackgroundTasks 级）
- 安全是 MVP：JWT、权限中间件，不是完整企业 IAM
- 已实现请求级 Turn Budget、检索质量门、答案三态发布门和 Tool 幂等/`unknown` 基础；尚未实现外部平台状态查询适配器、补偿 Tool 与完整 Saga

## 相关

- 架构 → [02](../02-architecture/README.md)
- 前端契约 → [08](../08-frontend-ux/README.md)
