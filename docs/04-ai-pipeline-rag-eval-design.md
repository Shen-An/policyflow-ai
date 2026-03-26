# 04. AI Pipeline、RAG 与评估详细设计

版本：v0.1  
日期：2026-07-09  
项目：Enterprise Policy Assistant  
技术基线：FastAPI + SQLite + LightRAG + BM25 预留 + Rerank 预留

---

## 1. 设计目标

本设计文档补充 `01-architecture-design.md`、`02-database-design-sqlite.md` 和 `03-api-design.md`，用于把 AI Pipeline、RAG 检索链路与评估体系细化成后续可实现的工程契约。

AI Pipeline 需要支持：

1. 基于 LightRAG 的制度问答；
2. 预留 BM25 关键词检索，方便后续做混合检索；
3. 预留 RerankService，方便后续接 reranker 模型；
4. 统一 Evidence 数据结构，避免上层 Agent 感知检索来源；
5. 预留 Hit@K、MRR、Recall@K 等检索指标；
6. 预留 RAGAS，用于评估回答忠实度、相关性和上下文质量；
7. 支持 Skill 编排、Tool 调用、MCP mock 和审计日志。

MVP 阶段必须坚持：

- LightRAG 是唯一实际可用的检索后端；
- BM25、Hybrid、Rerank、RAGAS 是明确的扩展点；
- 不要为了“看起来已支持”而返回伪结果；
- 不可用能力必须显式报错或标记 skipped，而不是静默返回空结果或 0 分。

---

## 2. AI Pipeline 总览

```text
UserQuestion
  ↓
RouterAgent
  ↓
PermissionFilter
  ↓
RetrievalAgent
  ↓
RAGService
  ↓
HybridRetriever / Strategy Dispatcher
  ├── LightRAGService
  └── BM25Retriever（预留）
  ↓
Optional RerankService
  ↓
AnswerAgent
  ↓
SkillAgent optional
  ↓
ComplianceAgent
  ↓
FinalResponse / Draft / Logs / Eval Traces
```

MVP 默认：

```text
retrieval_strategy = lightrag_only
rerank_enabled = false
```

后续扩展：

```text
retrieval_strategy = hybrid_lightrag_bm25
rerank_enabled = true
```

关键原则：

1. 权限过滤必须发生在检索前；
2. 制度类问题每轮必须重新检索；
3. 回答必须绑定本轮 Evidence；
4. 无 Evidence 时必须拒答，不能编造；
5. Agent 不直接访问数据库、LightRAG、BM25 或 reranker；
6. Agent 只消费统一 Evidence，不关心 Evidence 来自哪个检索器；
7. 检索 provenance、score、rank、rerank_score 进入日志、调试接口和评估结果。

---

## 3. 检索策略与 LightRAG Query Mode

必须区分两个概念：

### 3.1 Retrieval Strategy

用于决定使用哪些检索器。

```python
class RetrievalStrategy(str, Enum):
    LIGHTRAG_ONLY = "lightrag_only"
    BM25_ONLY = "bm25_only"
    HYBRID_LIGHTRAG_BM25 = "hybrid_lightrag_bm25"
```

含义：

| strategy | 含义 | MVP 状态 |
|---|---|---|
| `lightrag_only` | 只使用 LightRAG | 可用，默认 |
| `bm25_only` | 只使用 BM25 | 预留，默认不可用 |
| `hybrid_lightrag_bm25` | LightRAG + BM25 融合 | 预留，默认不可用 |

### 3.2 LightRAG Query Mode

用于配置 LightRAG 自身的查询模式。

```python
class LightRAGQueryMode(str, Enum):
    NAIVE = "naive"
    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"
    MIX = "mix"
```

注意：

```text
LightRAG query_mode = hybrid
```

不等于：

```text
retrieval_strategy = hybrid_lightrag_bm25
```

前者是 LightRAG 内部查询模式，后者是跨检索器混合检索策略。

---

## 4. 统一 Evidence 结构

所有检索器输出必须先转换为统一 Evidence，再交给上层 Agent。

```python
class Evidence(BaseModel):
    knowledge_base_id: str
    knowledge_base_name: str

    document_id: str | None = None
    document_title: str | None = None
    document_version: int | None = None

    chunk_id: str | None = None
    source_id: str | None = None

    snippet: str

    score: float | None = None
    rerank_score: float | None = None
    retriever_type: str
    rank: int

    metadata: dict[str, Any] = Field(default_factory=dict)
```

字段说明：

| 字段 | 说明 |
|---|---|
| `knowledge_base_id` | Evidence 所属知识库 ID |
| `knowledge_base_name` | Evidence 所属知识库名称，用于展示和审计 |
| `document_id` | 来源文档 ID；BM25 MVP 可按 document 级返回 |
| `document_title` | 来源文档标题 |
| `document_version` | 来源文档版本，用于审计和可复现评估 |
| `chunk_id` | 来源 chunk ID；拿不到稳定 chunk ID 时可为空 |
| `source_id` | 外部来源 ID，如 LightRAG source id |
| `snippet` | 给 AnswerAgent 使用的证据片段 |
| `score` | 原始检索分数或融合分数 |
| `rerank_score` | reranker 分数；未启用 rerank 时必须为空 |
| `retriever_type` | `lightrag` / `bm25` / `hybrid` 等，进入 trace/log |
| `rank` | 最终排序名次，从 1 开始 |
| `metadata` | 可扩展字段，不存放敏感对象或大段原文 |

### 4.1 Agent-facing Evidence 规则

虽然 Evidence 中保留 `retriever_type` 便于调试和审计，但业务 Agent 不应根据检索器来源改变回答逻辑。

推荐在实现中区分两个视图：

```text
RetrievalTraceItem：用于日志、debug、eval，保留 retriever_type / score / rerank_score
Evidence：用于 AnswerAgent / SkillAgent / ComplianceAgent，强调统一证据
```

也就是说：

- RAGService 可以持有完整 trace；
- RetrievalAgent 传给 AnswerAgent 的应是统一 Evidence；
- AnswerAgent 不直接判断 `retriever_type == "bm25"` 或 `retriever_type == "lightrag"`。

### 4.2 去重身份

推荐 Evidence / Candidate 具有确定性去重身份：

```text
优先级 1：document_id + chunk_id
优先级 2：document_id + normalized_snippet_hash
优先级 3：source_id
```

不要在无法稳定拿到 chunk_id 时伪造长期稳定 chunk_id。可以生成临时 trace id，但不能把它作为评估 ground truth。

---

## 5. RAGService 设计

后续实现文件：

```text
backend/app/services/rag_service.py
backend/app/rag/protocols.py
backend/app/rag/lightrag_adapter.py
backend/app/rag/bm25_retriever.py
backend/app/rag/hybrid_retriever.py
backend/app/rag/rerank_service.py
```

### 5.1 RetrievalRequest

```python
class RetrievalRequest(BaseModel):
    query: str
    knowledge_base_ids: list[str]
    strategy: RetrievalStrategy = RetrievalStrategy.LIGHTRAG_ONLY
    top_k: int = 5
    candidate_k: int | None = None
    rerank_enabled: bool = False
    lightrag_query_mode: LightRAGQueryMode = LightRAGQueryMode.HYBRID
```

约束：

1. `knowledge_base_ids` 必须已经过权限过滤；
2. `query` 不能为空；
3. `top_k` 必须有上限，例如 100；
4. `candidate_k` 在不开启 rerank 时默认等于 `top_k`；
5. 开启 rerank 后可以把 `candidate_k` 设置为 `max(top_k * 4, 20)`；
6. eval 和 retrieval-debug 可以显式指定 `top_k` / `candidate_k`。

### 5.2 Retriever Protocol

```python
class Retriever(Protocol):
    name: str

    @property
    def available(self) -> bool:
        ...

    async def retrieve(
        self,
        request: RetrievalRequest,
        limit: int,
    ) -> list[Evidence]:
        ...
```

所有检索器都输出 `Evidence` 或内部 `RetrievalCandidate`，由 RAGService 统一排序和转换。

### 5.3 RAGService 主流程

```text
RAGService.retrieve(request)
  ↓
validate request
  ↓
validate strategy availability
  ↓
dispatch retriever
  ↓
merge / deduplicate candidates
  ↓
optional rerank
  ↓
top_k truncate
  ↓
assign final rank
  ↓
return RetrievalResult(evidence, trace, warnings, latency_ms)
```

伪代码：

```python
async def retrieve(request: RetrievalRequest) -> RetrievalResult:
    candidate_k = resolve_candidate_k(request)

    if request.strategy == RetrievalStrategy.LIGHTRAG_ONLY:
        candidates = await lightrag.retrieve(request, candidate_k)

    elif request.strategy == RetrievalStrategy.BM25_ONLY:
        candidates = await bm25.retrieve(request, candidate_k)

    elif request.strategy == RetrievalStrategy.HYBRID_LIGHTRAG_BM25:
        candidates = await hybrid.retrieve(request, candidate_k)

    else:
        raise InvalidRetrievalConfigurationError(...)

    candidates = deduplicate(candidates)

    if request.rerank_enabled:
        candidates = await rerank_service.rerank(
            query=request.query,
            candidates=candidates,
            limit=request.top_k,
        )
        rerank_applied = True
    else:
        candidates = candidates[: request.top_k]
        rerank_applied = False

    return build_retrieval_result(candidates, rerank_applied)
```

---

## 6. LightRAG Adapter

后续实现文件：

```text
backend/app/rag/lightrag_adapter.py
```

职责：

1. 根据已授权知识库找到对应 LightRAG workspace；
2. 调用 LightRAG 查询接口；
3. 传入 `lightrag_query_mode`；
4. 将 LightRAG 输出转换为统一 Evidence；
5. 保留 document_id、chunk_id、source_id、score、snippet；
6. 捕获 LightRAG 异常并转换成项目内统一异常；
7. 不把 LightRAG 原始对象暴露给 Agent。

如果一个问题需要查询多个知识库：

- 只能查询权限过滤后的 KB；
- 不允许先查全部再过滤；
- 多 workspace 查询结果应进行确定性合并；
- 不同 workspace 的 score 不一定可直接比较。

MVP 可采用稳定 rank 合并策略，后续再引入 RRF 或权重融合。

---

## 7. BM25 预留设计

后续实现文件：

```text
backend/app/rag/bm25_retriever.py
```

### 7.1 MVP 行为

MVP 阶段 BM25 是显式 placeholder。

```python
class BM25Retriever:
    name = "bm25"

    @property
    def available(self) -> bool:
        return False

    async def retrieve(...):
        raise StrategyUnavailableError("BM25 retriever is not enabled")
```

原因：

- 现在没有真实 backend 源码；
- 数据库设计中尚未定义稳定 chunk 表；
- 如果返回空列表，会让调用方误以为“检索成功但无结果”；
- 显式不可用更利于 API、评估和前端处理。

### 7.2 未来 BM25 实现路径

未来第一版 BM25 可以基于：

```text
knowledge_documents.content_text
```

实现文档级关键词检索。

流程：

```text
authorized knowledge_base_ids
  ↓
load indexed knowledge_documents
  ↓
tokenize title + content_text
  ↓
rank-bm25 / SQLite FTS
  ↓
return document-level Evidence(chunk_id=None)
```

注意：

1. 不能伪造稳定 chunk_id；
2. 文档级 BM25 的 `chunk_id` 应为 `None`；
3. 后续如果增加 `knowledge_chunks` 表，再升级为 chunk 级 BM25；
4. 升级不应影响 AnswerAgent，因为 Evidence contract 不变。

---

## 8. Hybrid Retriever 预留设计

后续实现文件：

```text
backend/app/rag/hybrid_retriever.py
```

Hybrid Retriever 负责跨检索器融合：

```text
LightRAG candidates
BM25 candidates
  ↓
merge
  ↓
deduplicate
  ↓
fusion ranking
  ↓
optional rerank
```

MVP 阶段：

- 如果 BM25 不可用，`hybrid_lightrag_bm25` 必须显式不可用；
- 不要退化成 `lightrag_only`，否则评估结果会误导；
- error code 可命名为 `RETRIEVAL_STRATEGY_UNAVAILABLE`。

未来融合推荐使用 Reciprocal Rank Fusion（RRF）：

```text
rrf_score = Σ 1 / (rrf_k + rank_in_retriever)
```

推荐默认：

```text
rrf_k = 60
```

原因：

- LightRAG score 和 BM25 score 尺度不同；
- 直接比较 raw score 不可靠；
- RRF 对不同检索器分数尺度更稳健。

融合后 trace 应保留：

- 原始 retriever；
- 原始 rank；
- 原始 score；
- fusion score；
- final rank。

---

## 9. RerankService 预留设计

后续实现文件：

```text
backend/app/rag/rerank_service.py
```

### 9.1 默认行为

```text
rerank_enabled = false
```

默认关闭时：

- 保持候选顺序不变；
- 不调用外部模型；
- 不设置 `rerank_score`；
- `rerank_applied = false`。

### 9.2 开启但无 backend

如果请求：

```json
{
  "rerank_enabled": true
}
```

但系统未配置 reranker，则必须显式失败：

```text
RERANKER_UNAVAILABLE
```

不要静默 pass-through，否则用户会误以为 rerank 已生效。

### 9.3 未来 backend 约束

未来接入 reranker 后，必须满足：

1. reranker 只能重排输入候选，不得新增不存在的候选；
2. 输出必须保留 candidate_id；
3. `rerank_score` 单独记录，不覆盖原始 `score`；
4. trace 中保留 rerank 前 rank 和 rerank 后 rank；
5. reranker 失败时根据调用场景决定 fail-fast 或降级，但必须进入日志。

---

## 10. Retrieval Metrics 设计

后续实现文件：

```text
backend/app/evals/retrieval_metrics.py
```

指标包括：

- Hit@K
- MRR
- Recall@K
- first_relevant_rank

### 10.1 输入

```python
class RetrievalEvalInput(BaseModel):
    retrieved: list[Evidence]
    relevant_document_ids: list[str]
    relevant_chunk_ids: list[str] = []
    k_values: list[int] = [1, 3, 5, 10]
```

### 10.2 评估粒度

优先级：

1. 如果 `relevant_chunk_ids` 非空，并且检索结果中有稳定 `chunk_id`，使用 chunk 级评估；
2. 否则使用 document 级评估；
3. 不要在同一个 case 中混合 chunk 和 document denominator。

这与 `02-database-design-sqlite.md` 中的说明一致：如果 MVP 无法稳定拿到 LightRAG chunk_id，则先按 document_id 评估。

### 10.3 Hit@K

```text
Hit@K = 1, if top K contains at least one relevant item
Hit@K = 0, otherwise
```

示例：

```text
Relevant: [A, B]
Retrieved top 3: [X, Y, B]
Hit@3 = 1
```

### 10.4 MRR

```text
MRR = 1 / rank_of_first_relevant_result
```

如果没有 relevant result：

```text
MRR = 0
```

示例：

```text
Relevant: [A, B]
Retrieved: [X, B, A]
first relevant rank = 2
MRR = 1 / 2 = 0.5
```

### 10.5 Recall@K

```text
Recall@K = relevant items found in top K / total relevant items
```

示例：

```text
Relevant: [A, B, C]
Retrieved top 5: [X, B, Y, A, Z]
Recall@5 = 2 / 3 = 0.67
```

### 10.6 去重规则

计算指标前应对 retrieved IDs 去重：

```text
Retrieved: [X, B, B, A]
Deduped:   [X, B, A]
```

保留第一次出现的 rank。

### 10.7 空 ground truth

如果 `relevant_document_ids` 和 `relevant_chunk_ids` 都为空：

- 不应返回 0 分；
- 应标记为 invalid 或 skipped；
- 聚合指标时不计入 denominator。

---

## 11. RAGAS Hook 设计

后续实现文件：

```text
backend/app/evals/ragas_runner.py
```

RAGAS 是可选能力。

默认配置：

```json
{
  "ragas_config": {
    "enabled": false,
    "metrics": [
      "faithfulness",
      "answer_relevancy",
      "context_precision",
      "context_recall"
    ]
  }
}
```

### 11.1 输入结构

```python
class RagasEvaluationInput(BaseModel):
    question: str
    answer: str
    contexts: list[str]
    reference_answer: str | None = None
```

字段映射：

| 字段 | 来源 |
|---|---|
| `question` | eval case question |
| `answer` | AnswerAgent 输出 |
| `contexts` | Evidence.snippet 列表 |
| `reference_answer` | 未来可选参考答案字段 |

注意：

```text
expected_answer_keywords != reference_answer
```

不能把关键词列表直接伪装成 reference answer。

### 11.2 输出结构

```python
class RagasEvaluationResult(BaseModel):
    status: Literal["completed", "skipped", "failed"]
    metrics: dict[str, float] = {}
    reason: str | None = None
```

### 11.3 skipped 与 failed

以下情况应返回 skipped 或在启动 eval run 前拒绝：

- `ragas.enabled = false`；
- RAGAS 依赖未安装；
- evaluator LLM 未配置；
- embedding model 未配置；
- 所选 metric 缺少必要输入。

不要把运行失败写成：

```json
{"faithfulness": 0}
```

因为 0 是合法分数，不能代表系统失败。

### 11.4 安全与隐私

RAGAS 可能把 Evidence context 发给外部 evaluator。后续实现时必须明确：

1. 是否允许外发制度片段；
2. evaluator 使用哪个模型；
3. 是否记录 evaluator 请求；
4. 是否需要脱敏。

---

## 12. Eval Runner 设计

后续实现文件：

```text
backend/app/evals/eval_runner.py
backend/app/services/eval_service.py
```

### 12.1 运行流程

```text
EvalService.start_run()
  ↓
create eval_run(status=pending)
  ↓
EvalRunner.run()
  ↓
load eval cases / retrieval items
  ↓
for each case:
    build RetrievalRequest
    call RAGService.retrieve()
    compute Hit@K / MRR / Recall@K
    optional answer pipeline
    optional RAGAS
    write eval_result
  ↓
aggregate metrics
  ↓
update eval_run(status=success/failed)
```

### 12.2 Run Config Snapshot

建议后续在 `eval_runs` 增加或使用 JSON 字段保存完整配置快照：

```json
{
  "retrieval_config": {
    "strategy": "lightrag_only",
    "top_k_values": [1, 3, 5, 10],
    "rerank_enabled": false,
    "lightrag": {
      "query_mode": "hybrid"
    }
  },
  "ragas_config": {
    "enabled": false,
    "metrics": []
  }
}
```

原因：

- 评估需要可复现；
- 不能在 run 中途重新读取已改变的全局默认值；
- 后续比较 BM25 / Hybrid / Rerank 时需要知道当时配置。

### 12.3 Case Failure

单个 case 失败不应丢弃整个 run 的已完成结果。

推荐：

- 每个 `eval_results` 单独记录 `error_message`；
- 聚合指标只统计成功计算的 case；
- `eval_runs.metrics` 中记录：
  - `total_cases`
  - `completed_cases`
  - `failed_cases`
  - `skipped_cases`

---

## 13. API 设计补充

### 13.1 Retrieval Debug

接口沿用 `03-api-design.md`：

```http
POST /api/eval/retrieval-debug
```

请求建议扩展：

```json
{
  "query": "差旅报销需要哪些材料？",
  "knowledge_base_ids": ["uuid"],
  "strategy": "lightrag_only",
  "top_k": 10,
  "rerank_enabled": false,
  "lightrag": {
    "query_mode": "hybrid"
  }
}
```

响应应包含 trace：

```json
{
  "query": "差旅报销需要哪些材料？",
  "strategy": "lightrag_only",
  "lightrag_query_mode": "hybrid",
  "rerank_applied": false,
  "items": [
    {
      "rank": 1,
      "retriever_type": "lightrag",
      "document_id": "uuid",
      "chunk_id": null,
      "score": 0.82,
      "rerank_score": null,
      "snippet": "报销材料包括发票、行程单..."
    }
  ],
  "warnings": []
}
```

如果请求 `bm25_only` 但 BM25 未实现：

```json
{
  "error": {
    "code": "RETRIEVAL_STRATEGY_UNAVAILABLE",
    "message": "BM25 retriever is reserved but not enabled in MVP"
  }
}
```

### 13.2 Eval Run

接口沿用：

```http
POST /api/eval/runs
```

推荐请求结构：

```json
{
  "name": "MVP RAG 评估 2026-07-09",
  "case_ids": [],
  "retrieval_item_ids": [],
  "eval_types": ["retrieval", "rag_answer", "ragas"],
  "retrieval_config": {
    "strategy": "lightrag_only",
    "top_k_values": [1, 3, 5, 10],
    "rerank_enabled": false,
    "lightrag": {
      "query_mode": "hybrid"
    }
  },
  "ragas_config": {
    "enabled": false,
    "metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
  }
}
```

启动前应校验：

- strategy 是否可用；
- rerank 是否有 backend；
- RAGAS 是否有依赖和模型配置；
- top_k_values 是否合法；
- case 是否存在且 enabled。

---

## 14. 持久化映射

### 14.1 ai_query_logs.retrieved_sources

用于记录在线问答检索 trace。

建议结构：

```json
[
  {
    "rank": 1,
    "retriever_type": "lightrag",
    "retrieval_strategy": "lightrag_only",
    "lightrag_query_mode": "hybrid",
    "document_id": "uuid",
    "document_title": "差旅报销制度",
    "document_version": 3,
    "chunk_id": null,
    "score": 0.82,
    "rerank_score": null,
    "snippet": "报销材料包括发票、行程单..."
  }
]
```

注意：

- `query_mode` 字段只表示 LightRAG query mode；
- `retrieval_strategy` 应单独保存；
- 如果数据库暂时没有独立列，可先存在 JSON 中。

### 14.2 eval_results.retrieved_sources

保存评估当次的检索快照，不能只存 document IDs。

原因：

- 后续需要比较 LightRAG vs BM25 vs Hybrid；
- 需要复盘 rerank 前后排序；
- 需要解释 Hit@K / MRR / Recall@K。

### 14.3 eval_results.retrieval_metrics

示例：

```json
{
  "evaluation_level": "document",
  "hit_at_1": 0,
  "hit_at_3": 1,
  "hit_at_5": 1,
  "mrr": 0.5,
  "recall_at_5": 0.67,
  "first_relevant_rank": 2
}
```

### 14.4 eval_results.ragas_metrics

仅在 RAGAS 成功运行时写入分数：

```json
{
  "faithfulness": 0.82,
  "answer_relevancy": 0.76,
  "context_precision": 0.71,
  "context_recall": 0.68
}
```

如果 skipped：

```json
{
  "ragas_status": "skipped",
  "ragas_reason": "disabled"
}
```

---

## 15. 后续代码实现映射

未来创建 FastAPI backend 时，建议文件职责如下：

| 文件 | 职责 |
|---|---|
| `backend/app/schemas/retrieval.py` | Evidence、RetrievalRequest、RetrievalResult、strategy enum |
| `backend/app/rag/protocols.py` | Retriever、Reranker、DocumentSource 协议 |
| `backend/app/rag/lightrag_adapter.py` | LightRAG 查询与输出归一化 |
| `backend/app/rag/bm25_retriever.py` | BM25 placeholder，未来文档级 BM25 |
| `backend/app/rag/hybrid_retriever.py` | LightRAG + BM25 融合与去重 |
| `backend/app/rag/rerank_service.py` | Rerank pass-through 与后续 backend 接入 |
| `backend/app/services/rag_service.py` | RAG orchestration、strategy dispatch、trace 构建 |
| `backend/app/agents/retrieval_agent.py` | 调用 RAGService，把 Evidence 交给后续 Agent |
| `backend/app/evals/retrieval_metrics.py` | Hit@K、MRR、Recall@K 纯函数 |
| `backend/app/evals/ragas_runner.py` | RAGAS optional adapter |
| `backend/app/evals/eval_runner.py` | eval run 编排、结果聚合 |
| `backend/app/services/eval_service.py` | eval API 用例服务 |

---

## 16. 测试规划

后续代码实现时应增加以下测试。

### 16.1 Retrieval Metrics Unit Tests

覆盖：

1. rank 1 命中；
2. rank 2 命中；
3. 无命中；
4. 多个 relevant documents；
5. retrieved results 中有重复项；
6. K 大于结果长度；
7. chunk-level 评估；
8. document-level fallback；
9. 空 ground truth skipped / invalid；
10. 聚合指标计算。

示例：

```text
Relevant IDs: [A, B, C]
Retrieved:    [X, B, B, A]
Deduped:      [X, B, A]

Hit@1 = 0
Hit@3 = 1
MRR = 1/2
Recall@3 = 2/3
```

### 16.2 RAGService Unit Tests

覆盖：

- 默认只调用 LightRAG；
- `bm25_only` 在不可用时显式失败；
- `hybrid_lightrag_bm25` 在 BM25 不可用时显式失败；
- rerank disabled 时不改变顺序；
- rerank enabled 但无 backend 时显式失败；
- Evidence 与 trace 的字段符合预期；
- AnswerAgent 不依赖 retriever_type。

### 16.3 Adapter Tests

LightRAG fake client：

- query mode 正确传递；
- 只查询授权 KB；
- chunk_id 缺失时可正常返回 document-level evidence；
- backend error 转换为项目异常。

### 16.4 Eval Runner Tests

覆盖：

- retrieval eval run 正常写入 metrics；
- RAGAS disabled 返回 skipped；
- RAGAS missing dependency 不写 0 分；
- 部分 case 失败不丢失已完成结果；
- eval run 保存 config snapshot。

### 16.5 API Tests

覆盖：

- `/api/eval/retrieval-debug` 返回 trace；
- 不可用 strategy 返回稳定 error code；
- `/api/eval/runs` 校验 retrieval_config；
- `ragas.enabled=true` 但配置缺失时拒绝启动。

---

## 17. MVP 验收标准

当后续实现完成时，应满足：

1. 默认问答路径通过 LightRAG 检索；
2. BM25 和 Hybrid strategy 已在 schema/API 中预留；
3. 未实现的 BM25/Hybrid 不会静默返回空结果；
4. RerankService 默认关闭；
5. 未配置 reranker 时开启 rerank 会显式失败；
6. Evidence 结构统一，AnswerAgent 不感知检索来源；
7. `retrieval_strategy` 与 LightRAG `query_mode` 独立；
8. `ai_query_logs.retrieved_sources` 能记录检索 trace；
9. retrieval eval 能计算 Hit@K、MRR、Recall@K；
10. RAGAS hook 可选，disabled/missing dependency 能区分 skipped 与 0 分；
11. retrieval-debug 能观察 rank、retriever_type、score、rerank_score、snippet；
12. 所有未来扩展点都有明确文件和接口位置。
