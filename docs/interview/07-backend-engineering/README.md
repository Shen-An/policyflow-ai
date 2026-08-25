# 07. 后端工程面试点

> 这章是「后端工程点速查清单」,凝练是故意的(面试前快过)。**看不懂的词先查 [白话术语表](../00-glossary/README.md)**——尤其第 4 节(兜底:Turn Budget / 质量门 / 幂等 / unknown)和第 6 节(工程:WAL / N+1 / BackgroundTasks / JWT)。想看某点的展开详解,去对应章节。

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

#### 错误兜底四类（已落地基础版）

统一按四类问题设计，均已落地基础版（判断以确定性信号为主，非完整 Saga / 熔断）：

1. **调用次数失控**：在 Tool/Reflection 各自轮次限制之外，请求级 Turn Budget 再限单轮总 LLM 调用（`llm=16`）、总检索（`retrieval=2`）、总 Tool（`tool=8`）与整轮耗时（`180s`）；超限 `TURN_BUDGET_EXHAUSTED`，不再续跑也不编答案。
2. **RAG 有结果但质量差**：检索质量门识别空证据 / 偏题 / rewrite 漂移，最多回退原问题重检一次，仍不可靠就 hard refuse（`quality_gate.py`）。偏题判定优先用 cross-encoder 分数（低于阈值 → `RETRIEVAL_SCORE_BELOW_THRESHOLD`），拿不到真分数（默认聊天不开重排、或本地词法重排）时退回中文二元词覆盖率；两个信号都记，可用 40 条负样本量出拦住率与误拦率。
3. **答案有引用或事实问题**：Compliance 收敛成 `PASS / REVISE / REFUSE` 发布门；`REVISE` 定向改稿一次并复检，失败则安全拒答。
4. **外部操作失败或超时**：区分本地事务与外部副作用。外部写用幂等键、`unknown` 状态、禁止盲重发，而不是声称 DB rollback 能撤销邮件或飞书消息；补偿 Tool / 状态查询适配器仍待按真实连接器实现。

详细场景见 [`../11-scenario-questions`](../11-scenario-questions/README.md) Q11、Q12。

### 4. 后台任务意识

- 文档索引后台排队，避免导入接口阻塞  
- 文档更新韧性：版本号 +1 + `pending` 状态 + 后台重索引；commit 失败则 rollback 并删除孤儿文件（外部 LightRAG 索引不在事务内，不保证跨系统强一致）  
- LightRAG 超时 → BM25-only 且 metadata 打标（`fallback_reason=timeout`）；**非超时**失败直接抛，不静默降级  
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
