# PolicyFlow AI 错误兜底与可恢复性实施计划

版本：v0.2（基础落地）
日期：2026-08-12
状态：核心质量门已实施；外部状态查询、补偿 Tool、Memory outbox 仍为后续项

## 0. 本轮落地摘要

- 已实现请求级 Turn Budget：LLM、检索、Tool 次数和整轮耗时共享硬上限。
- 已实现 Retrieval Quality Gate：空证据、偏题、query rewrite 漂移；最多回退原问题一次。
- 已实现答案发布门：`PASS/REVISE/REFUSE`，改稿一次后复检，仍失败则安全拒答。
- 已实现 Tool 超时 `unknown`、幂等键、成功结果复用和禁止对未知状态盲目重发。
- 已实现 Chat 主要持久化路径 commit 失败 rollback，并把预算、检索质量、发布决策写入 diagnostics。
- 已在 `policyflow` conda 环境验证官方 LightRAG 1.5.4；进程内适配器使用离线可逆 tokenizer，避免首次运行依赖外部编码文件 CDN。
- 未实现真实外部平台的执行状态查询、补偿 Tool、高风险操作确认和生产级分布式 Saga。

## 1. 目标

把当前分散在 Prompt、RAG、Answer、Reflection、Compliance、Tool、MCP 和持久化层中的兜底能力，收敛为一条可解释、可配置、可测试的质量链路：

```text
请求
  → 全局调用预算 / 超时预算
  → Router / Query Rewrite
  → Retrieval
  → Retrieval Quality Gate
       ├─ 可靠：进入回答
       ├─ 可恢复：最多一次替代检索
       └─ 不可靠：hard refuse
  → Evidence-bound Answer（强制来源编号）
  → Deterministic Verifier
       ├─ PASS：允许发布
       ├─ REVISE：最多一次定向改稿并复检
       └─ REFUSE：替换为安全拒答
  → 原子持久化
  → Memory 异步写回 / 外部副作用核对与补偿
```

这仍然是统一 `AgentPipeline` 下的 tool-using RAG，不包装成伪 multi-agent，也不把 LLM 自检当唯一安全门。

## 2. 当前能力与主要缺口

| 能力 | 当前状态 | 主要缺口 |
|---|---|---|
| Prompt 约束“只按证据回答” | 已有：`answer_agent.py` | Prompt 只能降低风险，不能作为最终发布条件 |
| 无证据拒答 | 已有：默认 `CHAT_HARD_REFUSE_WITHOUT_EVIDENCE=true` | “有结果但质量差”主要依赖轻量词重叠，缺少统一质量分级和策略校准 |
| 来源编号 `[n]` | 已有：Prompt、Citation schema、前端引用 | 缺引用、悬挂引用、引用错配目前多为 warning，仍可能发布 |
| 生成后核查 | 已有：规则 Compliance + 高风险 Critique→Improve | Reflection 异常时会 fail-open；Compliance 尚未形成统一 `PASS/REVISE/REFUSE` 发布门 |
| LLM 重试 | 已有：可重试状态码、指数退避、最大尝试次数 | 只有 provider 层次数，没有“单轮总调用数 / 总时长 / 总成本”预算 |
| Tool / Reflection 最大轮数 | 已有：Tool 3 轮、Reflection 2 轮、Plan 5 步 | Tool 表中的 `timeout_seconds` 未真正约束 handler；各子系统预算未统一 |
| 错误账本 | 已有：`TurnState.errors[]`、request id、diagnostics | 缺少标准失败分类、降级决策、发布决策和聚合指标 |
| 数据库回滚 | 部分 service 手工 `rollback()` | Chat、Tool 日志、外部副作用、Memory 写回的事务边界不清；Tool 内部 `commit()` 会切断上层事务 |
| 外部 Tool/MCP 恢复 | 有超时和审计基础 | 缺少幂等键、unknown 状态、结果核对、补偿动作；副作用调用不能盲目重试 |

结论：用户提出的四层方案已经有基础，但需要从“提示词 + warning”升级为“强制质量门 + 有界恢复”。

## 3. 设计原则

1. **Fail closed 的场景**：无可靠证据、越权、悬挂引用、关键数字无依据、外部副作用结果未知。
2. **Fail open 的场景**：Memory 写回、非关键 diagnostics、可选 Reflection 服务不可用；必须记录 warning，不影响已有可靠回答。
3. **只重试瞬时且幂等的操作**：网络超时、429、部分 5xx；参数错误、权限错误、内容校验失败不重试。
4. **副作用工具默认不自动重试**：超时不等于未执行，必须先查状态，再决定补偿或人工确认。
5. **限制总预算，而不只限制单节点轮数**：防止 Router、检索、Tool、Critique 各自“合法重试”，合计造成调用爆炸。
6. **规则门优先于 LLM judge**：引用编号、数字、权限、空证据等可确定问题先用确定性代码检查。
7. **回滚必须诚实**：SQLite 事务只能回滚本地未提交数据；邮件、日历、飞书等外部动作需要幂等与补偿，不能宣称数据库 rollback 能撤销。

## 4. 实施波次

### Wave 1：统一失败模型、调用预算和超时

目标：任何一轮 Chat 都有明确的最大调用次数、最大耗时和失败分类。

建议新增：

- `backend/app/agents/guardrail_policy.py`
  - `FailureClass`：`transient` / `quality` / `permission` / `validation` / `side_effect_unknown` / `internal`
  - `GuardrailAction`：`retry` / `degrade` / `revise` / `refuse` / `abort`
- `backend/app/agents/turn_budget.py`
  - 统计 LLM、retrieval、tool、reflection 调用数和累计耗时
  - 超限统一产生 `TURN_BUDGET_EXHAUSTED`
- `backend/app/schemas/guardrail.py`
  - `GuardrailDecision`、`BudgetSnapshot`、`ReleaseDecision`

已落地默认值（源：`backend/app/core/config.py`，最终以代码为准）：

```text
CHAT_TURN_TIMEOUT_SECONDS=180          # 整轮 Chat 总耗时预算（硬截止）
CHAT_TURN_MAX_LLM_CALLS=16             # Turn Budget：单轮 LLM 调用上限
CHAT_TURN_MAX_RETRIEVAL_ATTEMPTS=5     # Turn Budget：单轮检索次数上限（2026-08-28 由 2 放宽）
CHAT_TURN_MAX_TOOL_CALLS=8             # Turn Budget：单轮 tool 调用总上限
CHAT_TOOL_MAX_ROUNDS=3                # Answer tool loop 轮数上限（区别于上面的总数 8）
CHAT_REFLECTION_MAX_ROUNDS=2          # Critique→Improve 反思轮数
CHAT_ANSWER_REVISE_MAX_ROUNDS=1       # 回答修订轮数
CHAT_TOOL_DEFAULT_TIMEOUT_SECONDS=20  # 单次 tool 默认超时
```

检索额度为什么是 5：一次提问里主检索占 1 次（质量门回退原问题时占 2 次），L2 计划里每个 `retrieve` 步骤各占 1 次，Answer 循环的补充 `kb.search` 再占。原值 2 意味着补查最多 1 次、主检索重试过就是 0 次，`kb.search` 必然抛 `TURN_BUDGET_EXHAUSTED` 并在 UI 上显示成红色「工具失败」。现在额度触顶属于**有界降级**：`kb.search` 返回 `degraded=true` + 「基于已有证据回答，不足就直说」，Answer 循环记 `status="warning"`，budget 快照照旧显示 `retrieval=n/5`。

实施要点：

- 将 LLM provider 的重试与 Chat 单轮预算分开：provider 重试消耗同一预算。
- 对重试增加 jitter；尊重 `Retry-After`。
- 只对 408、425、429、可恢复 5xx 和网络错误重试。
- ToolRegistry 使用工具表中的 `timeout_seconds`，通过 `asyncio.wait_for` 真正执行超时。
- 检索、Critique、Improve、MCP 都写入同一 `BudgetSnapshot`。
- 预算耗尽后不再继续反思或工具循环，进入已定义的降级/拒答路径。

验收：

- 任意失败组合都不能突破总调用上限。
- 非重试错误只调用一次。
- Tool handler 超时后返回稳定错误码并写入审计日志。
- SSE 能显示“重试中 / 已降级 / 预算耗尽”，但不暴露内部敏感异常。

### Wave 2：RAG 质量门与有界替代检索

目标：不再把“返回了若干文档”等同于“检索可靠”。

建议新增 `backend/app/rag/quality_gate.py`，输出：

```text
decision: accept | retry | refuse
reason_codes: [...]
signals:
  evidence_count
  question_overlap
  top1_score
  rerank_score
  query_rewrite_drift
  source_diversity
  synthetic_score_ratio
```

质量判定至少覆盖：

- 空结果：`NO_RELIABLE_EVIDENCE`
- 结果与原问题偏题：沿用并增强 `question_evidence_support`
- Query rewrite 漂移：改写查询有命中，但与原始问题低相关
- 仅 synthetic score：不可直接当真实相似度阈值
- Top-K 内容重复或来自同一无关文档
- 文档版本、授权知识库和物理删除状态不合法
- 关键实体、金额、时间等问题词在证据中完全缺失

有界恢复顺序：

1. 第一次使用当前 Router 选择的 query + strategy。
2. 若 rewrite 漂移或低相关，最多再检索一次：优先回退到原始问题；必要时使用明确的替代策略（如 Hybrid ↔ BM25）。
3. 两次都不通过质量门，直接 hard refuse；不继续让 LLM“凭经验补全”。

重要边界：

- 不为 LightRAG、BM25、RRF synthetic score 共用一个拍脑袋阈值。
- 阈值按检索策略分别用 `eval_test` 数据校准。
- Chat 与 Eval 继续共用 `AgentPipeline` 和质量门，避免线上与评测两套逻辑。

验收指标：

- 现有 Hit@1 / Hit@5 / Hit@10 / MRR 不回退。
- 新增负样本集：无答案、偏题、相似术语但不同制度、过期版本、错误知识库。
- 负样本的“错误放行率”作为主兜底指标；同时记录 false refusal，避免一味拒答。
- 替代检索最多一次，调用次数可在 diagnostics 中核验。

### Wave 3：答案发布门——引用、逐条核查、定向改稿、最终拒答

目标：回答生成后必须通过发布门，不再仅把严重问题记成 warning。

建议把当前 Compliance 扩展为三态：

```text
PASS    → 可发布
REVISE  → 带明确 issue 列表改稿一次，再重新校验
REFUSE  → 不发布原稿，替换为安全拒答或仅保留有证据的部分
```

确定性硬检查：

- 有制度事实但没有 `[n]` 引用
- 引用编号超出证据范围
- 引用与对应证据明显错配
- 金额、比例、天数、次数等硬数字未出现在证据中
- 证据为空但答案不是拒答
- 答案声称执行了未成功的 Tool / MCP
- Mock MCP 结果未明确标注 `status=mock`
- 记忆内容被写成制度依据

改稿策略：

1. 先运行确定性 Verifier。
2. 对 `REVISE`，将结构化 issue 列表交给 ImproveAgent，最多一次。
3. 改稿后重新运行同一 Verifier，禁止“改完不复检”。
4. 仍有硬错误则 `REFUSE`；可以删除无依据句子并返回部分可靠答案，但不能保留问题原稿。
5. CritiqueAgent 继续只用于高风险语义检查，不替代规则门。

对当前 Reflection 的调整：

- Critique JSON 解析失败时，不能自动把有硬错误的原稿放行。
- 若确定性规则已 PASS，Critique 不可用可以 fail-open 并记录 warning。
- 若确定性规则未 PASS，Critique/Improve 不可用必须 fail-closed。
- `CHAT_REFLECTION_MAX_ROUNDS=2` 保持硬上限；发布门额外改稿最多 1 次，且计入全局预算。

来源编号的产品契约：

- Prompt 继续要求 `[1]`、`[2]`。
- Evidence rank 在最终生成前冻结；改稿与 Tool 补检索后需要重新编号。
- 后端根据最终证据生成 `citations`，不能只信模型文本。
- 前端来源卡与正文编号必须一一对应；悬挂引用不得展示为成功回答。

验收：

- 无引用、悬挂引用、错误引用、无依据数字均有单测。
- 硬错误在最终 ChatResponse 中不能以 `completed + compliance.passed=true` 出现。
- 改稿一次仍失败时，最终内容为安全拒答/可靠部分，不是原始草稿。
- hard refuse 不触发无意义 Reflection。

### Wave 4：事务、幂等、补偿和故障恢复

目标：明确“哪些能 rollback，哪些只能补偿”，防止重复执行和半成功状态。

#### 4.1 本地数据库事务

- 将用户消息、助手消息、`AIQueryLog`、Conversation 更新时间放入同一 Unit of Work。
- 持久化任一步失败时统一 `session.rollback()`，不留下只有 user message 或只有 query log 的半成品。
- 避免 ToolRegistry 在上层 Chat 事务中直接 `session.commit()`；改为：
  - 使用独立审计 session，或
  - 由上层控制 commit，ToolRegistry 只 `flush()`。
- 对本地写操作使用 savepoint；单个可选步骤失败时可回滚该步骤，不污染整轮状态。

#### 4.2 Memory 写回

- Memory 是辅助能力，不应因写回失败撤销一个已验证答案。
- 将 writeback 改成幂等后台任务 / outbox：`pending → running → succeeded/failed`。
- 使用 `turn_id + memory_type + content_hash` 去重。
- 限次重试，超过次数进入 dead-letter/诊断状态，不无限重试。

#### 4.3 外部 Tool / MCP 副作用

对 Tool 增加元数据：

```text
operation_type: read | local_write | external_write
idempotent: bool
requires_confirmation: bool
compensation_tool: optional
```

每次副作用调用生成：

```text
idempotency_key = request_id + turn_id + tool_call_id
status = pending | succeeded | failed | unknown | compensated
```

处理规则：

- `read`：可按瞬时错误策略有限重试。
- `local_write`：受本地事务保护。
- `external_write`：默认不自动重试；超时后标为 `unknown`，先通过查询工具核对是否已执行。
- 高风险或不可逆 Tool 在执行前要求用户确认。
- 若外部执行成功但后续回答持久化失败，记录明确告警，不能宣称“已回滚”；如支持则触发补偿工具，否则提示人工处理。
- Mock 企业连接器继续明确返回 `status=mock`，不进入真实副作用叙事。

验收：

- 同一幂等键重复提交不会产生两次副作用。
- 外部调用超时不会盲目再次发送。
- DB commit 失败会回滚本地记录。
- Memory 写回失败不会影响已经发布的可靠答案。
- 能区分 `failed`（确定失败）与 `unknown`（可能已经执行）。

## 5. 可观测性与错误码

统一错误码建议：

| 类别 | 示例错误码 | 默认动作 |
|---|---|---|
| 调用预算 | `TURN_BUDGET_EXHAUSTED` | 降级或拒答 |
| LLM | `LLM_TIMEOUT` / `LLM_RATE_LIMITED` | 有界重试，随后降级 |
| 检索 | `RETRIEVAL_LOW_QUALITY` / `QUERY_REWRITE_DRIFT` | 最多一次替代检索 |
| 证据 | `NO_RELIABLE_EVIDENCE` | hard refuse |
| 引用 | `MISSING_CITATIONS` / `DANGLING_CITATIONS` | 改稿一次，仍失败则拒答 |
| Tool | `TOOL_TIMEOUT` / `TOOL_NOT_ALLOWED` | 中止该 Tool；按风险决定是否继续回答 |
| 副作用 | `SIDE_EFFECT_STATUS_UNKNOWN` | 禁止自动重试，进入核对/补偿 |
| 持久化 | `CHAT_PERSIST_FAILED` | DB rollback |
| Memory | `MEMORY_WRITEBACK_FAILED` | fail-open + 后台重试 |
| 模型下线 | `EMBEDDING_MODEL_UNAVAILABLE` | 立即失败（不重试），提示换模型 + 重新索引 |

### 5.1 上游模型下线（EOL）不是「网络抖动」

2026-08-25 NVIDIA 一次性下线了三个我们在用/备用的模型：`nvidia/llama-nemotron-embed-1b-v2`（Embedding 主力）、`nvidia/llama-nemotron-rerank-1b-v2`、`nvidia/rerank-qa-mistral-4b`，调用返回 **410 Gone**，body 里写明 end-of-life 时间。

处理口径（已落地）：

- **410 / 404 不重试**。`OpenAICompatibleEmbeddingService` 把这两个状态码判为「模型/端点不存在」，第一次响应就抛 `EMBEDDING_MODEL_UNAVAILABLE`，不再走「多种 payload 形状兼容」和退避重试——重试只会把同一个死模型打三遍，还让报错信息变成「共 3 次兼容尝试」，掩盖真实原因。
- **报错要可执行**：带上模型名、上游原文、以及「去模型设置换模型 + 换完要重新索引」的提示。
- **Reranker 配置只留在服役的模型**（`NVIDIA_RERANKER_MODELS`）。轮换 + 逐个兜底的代码保留，死模型留在列表里只会浪费一次调用。
- **换 Embedding 模型 = 向量空间变了**。即使新旧模型维度相同（本次都是 2048，所以不会报维度错），旧向量和新 query 向量不可比，检索质量会悄悄变差。换完必须重建索引：`python scripts/reindex_lightrag.py --kb-code <code>`，或对单个文档调 `POST /api/documents/{id}/index`。引用 Hit@K / MRR 之前，`eval_test` 必须先重建索引，否则数字不可比。

建议在 `TurnDiagnostics` 增加：

### 5.2 Planner 编出来的 Skill 名不等于「Skill 缺失」

2026-08-28 从 `ai_query_logs` 里挖到 4 轮 `STEP_ERROR_SKILL / Skill not found`。看着像 Skill 没注册，实际是 **Router LLM 把 `plan_steps[].skill_hint` 写成了描述性中文**：`流程清单抽取`、`报销流程解析技能：识别制度中的报销条件、审批链、材料清单`。`skills` 表和 handler 都没问题，是这两个名字根本不存在。

处理口径（已落地）：

- `backend/app/skills/catalog.py` 是唯一真源：`IMPLEMENTED_SKILLS = process_checklist | policy_compare | summary`，`resolve_skill_name()` 做「精确名 → 别名 → 关键词（对比/摘要/清单流程步骤）」三级归一。
- **所有 planner 出口都过归一**：`router_agent._coerce_plan_steps`、`plan_normalize._coerce_step`（ToT 分支计划也走它）、`PlanExecutor` 执行前、`SkillAgent.execute_one`、`skill.run` 工具入口。归一不出结果就**不猜**。
- Router / ToT 的 prompt 明确约束 `skill_hint` 只能取这三个之一，否则填 `null`。
- 归不了的名字：`SkillAgent.execute_one` 返回 `status="skipped"` + 「未注册的 Skill『X』，已跳过」，PlanExecutor 记 info 级 `skipped`，**不再是红色硬失败**；模型自己调 `skill.run` 写错名时返回 `degraded=true` 并告知可用清单，让它换一个而不是吃一句 404。
- 这条边界要在面试里说清：**能跑的 Skill 只有 3 个**，`skills` 表里另外 4 行（`knowledge_qa` / `application_draft` / `faq_generate` / `risk_check`）没有 handler，是种子数据，不是能力。

- `budget`：已用/最大调用数、耗时
- `retrieval_quality`：信号、判定、是否发生替代检索
- `release_decision`：PASS/REVISE/REFUSE、原因码
- `degraded_components`：Memory、Reflection、MCP 等降级项
- `side_effects`：只展示脱敏后的状态，不泄露密钥和完整参数

生产/演示指标：

- `hard_refuse_rate`
- `false_refusal_rate`（离线标注集）
- `citation_valid_rate`
- `claim_support_rate`
- `retrieval_retry_rate` / `retrieval_retry_success_rate`
- `llm_retry_rate` / `budget_exhausted_rate`
- `tool_timeout_rate`
- `side_effect_unknown_rate`
- Chat P50/P95 延迟和每轮 LLM 调用数

检索主指标仍然是 Hit@1 / Hit@5 / Hit@10 / MRR，并写清策略和 N；兜底指标不能替代检索质量指标。

## 6. 测试与故障注入计划

### 单元测试

- provider：429→成功、连续 5xx、不可重试 400、超时、预算耗尽
- Tool：真实 timeout、白名单拒绝、handler 异常、最大轮数
- RAG gate：空结果、偏题结果、rewrite 漂移、synthetic score、过期/删除文档
- Verifier：缺引用、悬挂引用、错配引用、无依据数字、Memory 冒充制度
- Reflection：Critique 解析失败、Improve 空结果、达到最大轮数
- 事务：flush/commit 失败后无半成品数据
- 幂等：重复调用只产生一个外部 operation

### 集成测试

- `/api/chat` 与 `/api/chat/stream` 使用同一发布决策。
- SSE 中途异常能发稳定 `error` 事件，数据库状态一致。
- Tool 成功、答案失败、持久化失败等组合场景可追踪。
- Memory writeback 故障不会改变 Chat 主结果。
- Eval 走同一 Retrieval Quality Gate，但默认关闭成本高的 Reflection。

### 离线数据集

- 在 `eval_test` 增加至少 50 条兜底用例，分类包含：
  - 无答案问题
  - 相似词但错误制度
  - 多知识库串库
  - 短跟进句 rewrite 漂移
  - 金额/天数诱导幻觉
  - 要求不存在模板
  - 过期制度与当前制度冲突
- 混入不少于 200 篇干扰文档，避免小库虚高。

## 7. 面试高频场景口径

| 场景 | 系统行为 | 诚实边界 |
|---|---|---|
| LLM 连续限流 | 指数退避 + jitter + 最大次数 + 单轮总预算；耗尽后返回可解释错误 | 不会无限重试，也不保证第三方服务必然恢复 |
| RAG 返回 5 条但都不相关 | 质量门拒绝；最多一次回退原问题/替代策略；仍差则 hard refuse | “有 Top-K”不代表有可靠证据 |
| 模型答案带错来源编号 | 后端 Verifier 阻止发布，定向改稿一次并复检 | Prompt 要求引用不是强保证，必须有后置校验 |
| Critique 自己报错 | 确定性规则已通过时可降级；规则未通过时 fail-closed | 不把 LLM 自检当唯一裁判 |
| Tool 调用了很多次 | Tool 轮数、全局调用数、总时长三层限制 | 单节点 max round 不能防止整条链路调用爆炸 |
| 外部发消息超时 | 标记 `unknown`，先查执行状态，不盲目重发 | 超时不等于没执行，数据库 rollback 也撤不回外部消息 |
| Chat 保存失败 | 本地消息和 query log 同事务 rollback | 若外部副作用已经成功，只能核对/补偿，不能假装全局回滚 |
| Memory 写回失败 | 回答照常返回，写回进入后台有限重试 | Memory 非权威、非主链路事务 |
| MCP 服务不可用 | read 类能力降级；关键外部操作中止并提示 | Mock 连接器必须继续标 `status=mock` |
| 用户重复点击发送 | request/turn 幂等键去重；副作用工具返回原 operation 状态 | 幂等需业务端或适配器支持，不能只靠前端禁用按钮 |

## 8. 推荐开发顺序与交付物

### PR 1：Guardrail 基础设施

- 失败分类、TurnBudget、统一配置、Tool timeout
- 单元测试与 diagnostics

### PR 2：Retrieval Quality Gate

- 质量信号、最多一次替代检索、策略阈值校准
- 负样本评测与 Chat/Eval 同路验证

### PR 3：Answer Release Gate

- Compliance 三态、引用硬门、一次改稿复检、最终安全拒答
- 前端展示发布/降级原因

### PR 4：事务与副作用恢复

- Chat Unit of Work、Tool commit 边界调整
- Memory outbox、Tool/MCP 幂等键、unknown/compensated 状态
- 故障注入测试与面试文档更新

每个 PR 都应满足：代码、测试、配置说明、错误码、演示步骤同步提交；不要最后一次性补文档。

## 9. 完成标准

- [ ] 单轮总调用数、Tool 轮数、Reflection 轮数、检索次数均有硬上限。
- [ ] “有检索结果但低质量”能够触发替代检索或拒答。
- [ ] 缺引用、悬挂引用、关键数字无依据不能直接发布。
- [ ] 改稿后必复检，失败后不返回原始问题草稿。
- [ ] Tool 的 `timeout_seconds` 真正生效。
- [ ] 本地持久化失败可 rollback，无半成品 Chat 记录。
- [ ] 外部副作用具备幂等、unknown 状态和补偿/人工核对路径。
- [ ] Memory 写回失败不影响主回答，并有有限重试与诊断。
- [ ] Chat 与 Eval 使用同一质量门和发布门。
- [ ] 面试文档能明确回答“为什么不无限重试”“低质 RAG 怎么拒答”“外部操作怎么回滚”。

## 10. 明确不做

- 不引入 CrewAI/AutoGen 角色群聊来包装错误处理。
- 不把 LLM-as-judge 当作唯一事实核查器。
- 不宣称 SQLite 事务可以回滚邮件、日历、飞书等外部副作用。
- 不对非幂等外部工具自动无限重试。
- 不在未做策略校准时用一个统一 score 阈值判断所有检索器。
- 不因加入兜底就夸大为“生产级分布式 Saga / 熔断平台”；本阶段是单体应用内的有界恢复与诚实补偿。
