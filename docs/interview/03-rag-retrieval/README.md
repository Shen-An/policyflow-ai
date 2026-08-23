# 03. RAG 检索：Hybrid / BM25 / Rerank

## 一句话

检索是 **Service**；默认 Hybrid（LightRAG 路径 + BM25 融合，LightRAG 超时自动降级 BM25 且打标）。Rerank 默认 **本地 lexical fusion**（词法融合，非模型）；另有 **可选真实 NVIDIA cross-encoder**（NIM 云端，opt-in、按 run 选择、失败不静默回退）。

## 面试可讲结构

```text
Query（可经 rewrite）
  → LightRAG / 向量侧候选（超时→降级 BM25-only，metadata 打标 fusion=bm25_fallback）
  → BM25 词法候选
  → 融合（如 RRF）
  → 可选 rerank（默认 local lexical fusion / 可选 NVIDIA cross_encoder）
  → Evidence[]（snippet + rank + score + metadata）
  → 质量门（空证据 / 偏题 / rewrite 漂移 → 最多回退原问题重检一次，仍不行则清空证据 hard refuse）
```

## 白话详解（检索一步步怎么发生）

上面那串黑话,拆成「去图书馆找资料」的步骤:

1. **你的问题进来**(可能先被 query rewrite 补全——比如「给我模板」补成「给我差旅报销模板」)。

2. **两路同时找**:
   - **一路 LightRAG / 向量**:按「意思」找。把问题变成一串数字,找数字相近的段落。*你问「休假」也能找到写「年假」的。*
   - **一路 BM25**:按「关键词字面」找。老牌算法,快、稳、好解释。*你问「年假」就找有「年假」俩字的。*
   - 如果 LightRAG 那路**卡太久(超时)**,就只用 BM25 那路,并**打个标签**说明「降级过」(`fallback_reason=timeout`)——是明着标,不是偷偷降。非超时的错(比如密钥错)直接报错,不降级。

3. **两路结果用 RRF 合并**成一个总排名(把两路各自的排名公平拼一起,不是简单相加分数)。

4. **可选 rerank(再排一次)**:
   - **默认本地 lexical fusion**:比词重合度,**不用模型、不花钱**,结果里标 `reranker_method=local_lexical_fusion`。
   - **可选真 NVIDIA cross-encoder**:模型「逐条精判」相关度,是**真云端服务**,**默认关、要钱、opt-in**(评估页按 run 选)。选了却用不了直接报 503,**不偷偷降回本地**——免得评测数字张冠李戴。

5. **去重、取前 K 条**,作为这一轮的「证据」(`Evidence[]`),每条带出处段落、排名、分数。

6. **质量门检查**:结果空 / 跑题 / rewrite 改写跑偏了 → 用**原问题**重找一次(最多一次),还不行就**清空证据、hard refuse**(拒答)。*避免拿一堆跑题的段落硬编答案。*

▸**面试可以说**:「检索默认两路一起找:向量按意思、BM25 按字面,用 RRF 合并。向量那路超时就降级只用 BM25 并打标,不偷偷降。可选 rerank:默认本地词法版不花钱,另有可选的真 NVIDIA 云端重排,默认关、失败直接报错不静默降级。最后过质量门,跑题就用原话重找一次,还不行就拒答。」

**别夸大(红线):**
- 默认 rerank 是**本地词法,不是模型**——别说「自研 BGE / 默认在线 / 生产级低延迟」。
- Hybrid 在「一篇文档整篇匹配」的任务上**未必比 BM25 明显好**——没区分度就老实说接近,别写「Hybrid 显著更优」。
- LightRAG 有些路径的分数是**合成衰减**(`score_is_synthetic=true`),别当真实语义相似度吹。

---

## 公式与概念详解（LightRAG / BM25 / RRF / NVIDIA rerank）

上面是白话,这里给**能写白板、能应对追问**的公式和概念。代码都在 `backend/app/rag/`。

### 1. BM25 —— 按关键词字面打分的老牌算法

**概念**:把问题和文档都切成词,看文档里出现了多少个问题里的词、出现得多频繁、以及这个词有多「稀罕」(越稀罕,命中越值钱)。是搜索引擎用了几十年的标配。

**公式**(标准 BM25,我们用 `rank_bm25` 库的 `BM25Okapi`,默认 k1≈1.5、b≈0.75):

```text
score(q, d) = Σ_{t∈q}  IDF(t) · f(t,d)·(k1+1) / ( f(t,d) + k1·(1 − b + b·|d|/avgdl) )

IDF(t) = ln( (N − n(t) + 0.5) / (n(t) + 0.5) + 1 )
```

| 符号 | 含义 |
|---|---|
| `f(t,d)` | 词 t 在文档 d 里出现几次(词频) |
| `IDF(t)` | 词 t 的稀有度:全库 N 篇里有 n(t) 篇含它,越少越值钱 |
| `|d| / avgdl` | 这篇文档长度 / 平均文档长度(b 项用它做长度归一,长文档不至于靠字多占便宜) |
| `k1` | 词频饱和点:词频涨到一定程度后加分变慢(防止一个词刷屏刷出高分) |
| `b` | 长度归一强度(0=不管长短,1=完全按长度打折) |

**我们这儿的实现细节**(`bm25_retriever.py`):
- **文档级**打分,整篇 `content_text` 一起算(不是按切块 chunk)。
- **分词**:英文按 `[a-z0-9_]+` 切词;**中文切成「相邻两字一组」**(bigram,比如「年假政策」→「年假」「假政」「政策」)。*这样中文没有分词器也能做字面匹配,简单但够用。*
- 优点:快、稳、可解释、专有名词/条款号(如「差-2024-003」「年假管理办法」)字面命中强。

- 1. **关键词出现加分**：作文中提到 "大模型" 加 1 分，提到 "课程" 加 1 分
2. **关键词稀有度加分**："大模型" 是比较稀有的词，加 2 分；"课程" 是比较常见的词，加 1 分
3. **词频边际递减**：第一次提到 "大模型" 加 2 分，第二次加 1 分，第三次加 0.5 分，之后再提就不加分了
4. **文章长度扣分**

### 2. LightRAG —— 我们用的「语义检索」那一路

**概念**:LightRAG 是一个开源 RAG 框架(HKUDS 出品),特点是**知识图谱 + 向量混合**——不光按字面找,还能抽实体/关系,按「意思」找。*你问「休假」,能找到写「年假」的段落。*

**我们怎么用**(`lightrag_adapter.py`):
- 它在我们系统里是**一个独立 REST 服务**,不是进程内库。我们通过 HTTP 把文档 `POST /documents/text` 灌进去、再 HTTP 查询拿候选段落。
- **按知识库隔离 workspace**:每个知识库一个独立 endpoint(URL 带 `workspace=<kb_code>`),互不串。
- 它是 Hybrid 检索里的**语义/向量那一路**,跟 BM25 并行跑。
- **超时 45 秒**(`DEFAULT_LIGHTRAG_TIMEOUT_SECONDS=45`):卡超时只这一路降级成 BM25-only 并打标,不偷偷降。
- **诚实点**:有些路径返回的 `score` 是合成衰减分(`score_is_synthetic=true`),不是真实语义相似度——报指标别当它是模型相关分。

### 3. RRF —— 怎么把两路排名合并

**概念**:LightRAG 和 BM25 各给一份排名,分数不在一个量纲(一个 0~1、一个可能十几),不能直接加。RRF(Reciprocal Rank Fusion)不看分数、只看**排名**:排越靠前加分越多,两路都靠前就叠加。

**公式**(我们代码里 `k=60`,`hybrid_retriever.py`):

```text
contribution(rank) = 1 / (k + rank)        # k=60
score(d) = Σ_{检索器 r 命中了 d}  1 / (60 + rank_r(d))
```

*例:某文档在 LightRAG 排第 2、BM25 排第 5 → 总分 = 1/(60+2) + 1/(60+5) ≈ 0.0161 + 0.0154 = 0.0315。*
`k=60` 是个平滑常数,让排名靠后的也有点分、不至于断崖。代码里还按**文档级**去重对齐(BM25 命中过的文档不重复计入)。

### 4. Rerank —— 默认本地词法 vs 可选真 NVIDIA cross-encoder

**rerank 是干嘛**:检索粗筛出一批候选(几十条),rerank 再细排一次把最相关的往前挪。我们有两种:

#### 4a. 默认:`local_lexical_fusion`(本地词法融合,不花钱)

**公式**(`rerank_service.py`,默认 `lexical_weight=0.65, original_weight=0.35`):

```text
lexical = 0.8 · precision + 0.2 · coverage
  precision = |问题词 ∩ 证据词| / |问题词|     # 证据覆盖了多少问题词
  coverage  = |问题词 ∩ 证据词| / |证据词|     # 命中词占证据词多大比例
fused = 0.65 · lexical + 0.35 · original_score
# 若 original 是合成分(score_is_synthetic): fused = 0.85·lexical + 0.15·original
```

**特点**:纯词重叠,**不用模型、不花钱、本地跑、可解释**;metadata 标 `rerank_method=local_lexical_fusion`。
**短板**:抓不到同义改写(「休假」≠「年假」字面不重叠)——这正是要 cross-encoder 的理由。

#### 4b. 可选:`cross_encoder`(真 NVIDIA 云端重排,opt-in)

**概念**:cross-encoder 把「问题 + 候选段落」**一起**送进模型,让模型逐条精判相关度(不是分别编码再算距离的 bi-encoder)。准,但贵、慢。

**我们用的 NVIDIA NIM 模型**(`cross_encoder_rerank_service.py`,支持多模型轮换 + 逐个兜底):

```text
nvidia/llama-nemotron-rerank-vl-1b-v2   # 多模态版(VL),目前唯一在服役的
```

> **2026-08-25 上游下线(EOL)**:原来配了三个模型,其中 `nvidia/llama-nemotron-rerank-1b-v2`(文本版)和 `nvidia/rerank-qa-mistral-4b`(Mistral 4B)被 NVIDIA 下线了,调用直接返回 410 Gone。所以现在配置里只留 VL 这一个。**轮换 + 兜底的代码没删**——以后再加模型进配置就自动生效。

**请求 JSON**(发到 `https://ai.api.nvidia.com/v1/retrieval/{model}/reranking`):

```json
{
  "model": "nvidia/llama-nemotron-rerank-vl-1b-v2",
  "query": { "text": "差旅报销流程有哪些步骤" },
  "passages": [
    { "text": "差旅申请需先由直属主管审批……" },
    { "text": "年假申请应提前三个工作日……" }
  ],
  "truncate": "END"
}
```

**Headers**:`Authorization: Bearer <NVIDIA_API_KEY>`、`Content-Type: application/json`。
**响应 JSON**:返回 `rankings` 数组(也兼容 `results`/`data`),每项形如 `{"index": 0, "logit": 6.32}`——`index` 是候选在 `passages` 里的下标,`logit` 是相关度分;我们按 `logit` 降序重排,meta 记 `rerank_method=cross_encoder / rerank_provider=nvidia / rerank_model=… / rerank_score=…`。
`truncate=END` 表示超长段落从尾部截断。

**特点 / 为什么选它:**
| 点 | 说明 |
|---|---|
| 真 cross-encoder | query+passage 同过模型,抓同义/改写(「休假」↔「年假」),比词法准 |
| NVIDIA NIM 托管 | 真模型、API Key 即用,**不用自己租 GPU 部署**,省运维 |
| 多模型轮换+兜底 | 轮询起点(round-robin)分散负载;一个挂了自动试下一个,**全部挂才报错**(当前配置里只剩 VL 一个在服役) |
| **不静默降级** | 选了 cross_encoder 却全失败 → 直接 `RERANKER_UNAVAILABLE`(503),**绝不偷偷回退本地词法**——免得 eval 数字张冠李戴(报的是 cross-encoder 的分,用的却是词法) |
| opt-in + 按 run 选 | 默认关(要钱、有网络延迟);评估页可按 run / 按请求选,同一评测 A/B 两种 rerank |
| API Key 加密存 | 模型设置页三类服务(Chat/Embedding/Reranker)独立配置,Key 加密落库,测试连接对 NVIDIA 发一次真 1-passage rerank |

**诚实边界(必说)**:
- **默认不是它**——默认是本地词法;cross-encoder 是可选、要配 Key、要花钱。
- 云端调用有**网络延迟和配额**,没测严格 p99。
- 没宣称「自研 BGE / 默认在线 / 生产级低延迟」。

▸**面试可以说**:「rerank 两种:默认本地词法融合,0.65 词重叠 + 0.35 原分,不花钱可解释但抓不到同义;另有可选的真 NVIDIA cross-encoder,把问题+段落一起送模型精判,准但贵,三个 NIM 模型轮换兜底,全失败直接 503 不静默降级,保证评测数字对得上用的是哪种。」

---

## 关键实现落点

| 点                          | 代码/文档                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------- |
| Hybrid + 降级                | `backend/app/rag/hybrid_retriever.py`（LightRAG 超时→BM25-only，打标 `fallback_reason=timeout`）         |
| BM25                       | `backend/app/rag/bm25_retriever.py`                                                               |
| Rerank（默认，本地词法）            | `backend/app/rag/rerank_service.py`（`local_lexical_fusion`，词法重叠 0.65 + 原分 0.35，非模型）               |
| Rerank（可选，真 cross-encoder） | `backend/app/rag/cross_encoder_rerank_service.py`（NVIDIA NIM；`main.py` `_build_rerankers` 注册两种策略） |
| 检索质量门                      | `backend/app/rag/quality_gate.py` `assess_retrieval_quality`（accept / retry×1 / refuse）           |
| LightRAG 适配                | `backend/app/rag/lightrag_adapter.py` / in-process（离线 codepoint tokenizer）                        |
| 问题-证据支持度                   | `backend/app/agents/grounding.py` `question_evidence_support`                                     |
| 无证据 hard refuse            | `answer_agent.py` + `CHAT_HARD_REFUSE_WITHOUT_EVIDENCE`                                           |

## 必说边界（防穿帮）

1. **默认 Rerank 是本地词法，不是模型；但现在有真 cross-encoder 可选**  
   默认 `local_lexical_fusion`（词法重叠 0.65 + 原分 0.35，非模型；`rerank_enabled` 默认关），结果/trace 里可见 `reranker_method=local_lexical_fusion`。  
   另有 **`cross_encoder` = 真实 NVIDIA NIM 云端 rerank**（当前模型 `nvidia/llama-nemotron-rerank-vl-1b-v2`），**opt-in**：在评估页「页面选择」按 run / 按请求选。  
   **刻意不静默回退**：选了 cross_encoder 却未配置 / 调用失败 → 直接 `RERANKER_UNAVAILABLE`(503)，绝不偷偷降级回本地，避免 eval 数字张冠李戴；唯一回退是配置里多个 NVIDIA 模型之间轮换（2026-08-25 上游 EOL 后只剩 VL 一个，等于暂时没有同伴可换）。别说「自研 BGE」「默认在线」「生产级低延迟」。

2. **检索降级是超时才触发，且打标**  
   LightRAG 超时 → 降级 BM25-only，metadata 标 `fusion=bm25_fallback` / `fallback_reason=timeout`；**非超时**失败（如密钥错）直接抛错，不静默降级。别说「任何 LightRAG 失败都自动兜底」。

2. **Hybrid 不保证全面碾压 BM25**  
   在 1-doc 整篇匹配类任务上两者接近是常见现象；无区分度时不要写「Hybrid 显著更优」。

3. **LightRAG 分数可能是 synthetic**  
   部分路径 `score_is_synthetic=true`（rank decay），别当真实语义相似度吹。

4. **off-topic 过滤**  
   rewrite 后若证据与原问题 overlap 太低，pipeline 当无可靠证据处理，避免答非所问。

## 指标叙事（与 Eval 衔接）

- 主指标：**Hit@1 / Hit@5 / Hit@10 / MRR**
- 必须写清：**策略名 + N**（如 Hybrid, N=50）
- 评测语料只进 **`eval_test` 测试库**，禁止灌 hr/finance 业务库
- 导入应带干扰文档，避免小库 + 1-doc 金标虚高 100%

详见 [06-eval-metrics](../06-eval-metrics/README.md)。

## 高频 Q&A

**Q: 为什么 Hybrid？**  
A: 制度文本既有专有名词/条款编号（词法强），也有同义改写（语义强）；融合比单路更稳。但最终以评测数字为准，不预设 Hybrid 永远更好。

**Q: 候选怎么截断？**  
A: top_k / candidate_k 可配；评测与在线共用策略名，避免「演示一套、指标一套」。

**Q: 现在到底有没有 cross-encoder？用什么模型？**  
A: 有，但**不是默认**。默认 `local_lexical_fusion`（本地词法，非模型）；`cross_encoder` 是可选的**真实 NVIDIA NIM 云端 rerank**（`nvidia/llama-nemotron-rerank-vl-1b-v2`），按 eval run / 请求选。模型设置页可独立配置 chat / embedding / **reranker** 三类服务，reranker 连通性测试会对 NVIDIA 发一次真实 1-passage rerank。选 cross_encoder 但不可用直接 503、不静默降级——eval 数字永远对得上用的是哪种重排，可在同一评测上 A/B 两种 rerank。诚实 caveat：云端调用有网络延迟/配额，没测严格 p99。详见 [11 Q8](../11-scenario-questions/README.md)。

**Q: 证据如何进回答？**  
A: Answer prompt 带编号证据；要求 `[n]` 引用；Compliance/Verifier 做无证据/弱 grounding 告警。
