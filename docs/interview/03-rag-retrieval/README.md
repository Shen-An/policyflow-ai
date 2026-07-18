# 03. RAG 检索：Hybrid / BM25 / Rerank

## 一句话

检索是 **Service**；默认 Hybrid（LightRAG 路径 + BM25 融合），Rerank 默认是 **本地 lexical fusion**，不是 cross-encoder。

## 面试可讲结构

```text
Query（可经 rewrite）
  → LightRAG / 向量侧候选
  → BM25 词法候选
  → 融合（如 RRF）
  → 可选 local lexical fusion rerank
  → Evidence[]（snippet + rank + score + metadata）
  → grounding：off-topic 命中当无可靠证据
```

## 关键实现落点

| 点 | 代码/文档 |
|---|---|
| Hybrid | `backend/app/rag/hybrid_retriever.py` |
| BM25 | `backend/app/rag/bm25_retriever.py` |
| Rerank | `backend/app/rag/rerank_service.py`（`local_lexical_fusion`） |
| LightRAG 适配 | `backend/app/rag/lightrag_adapter.py` / in-process |
| 问题-证据支持度 | `backend/app/agents/grounding.py` `question_evidence_support` |
| 无证据 hard refuse | `answer_agent.py` + `CHAT_HARD_REFUSE_WITHOUT_EVIDENCE` |

## 必说边界（防穿帮）

1. **Rerank ≠ cross-encoder**  
   默认本地词法融合；trace 里应能看到 `rerank_method=local_lexical_fusion`。

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

**Q: 证据如何进回答？**  
A: Answer prompt 带编号证据；要求 `[n]` 引用；Compliance/Verifier 做无证据/弱 grounding 告警。
