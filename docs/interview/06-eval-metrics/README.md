# 06. 评估：Hit@K / MRR / CRUD

## 一句话

主指标 **Hit@1 / Hit@5 / Hit@10 / MRR**，必须写清 **检索策略 + N**；  
金标走 CRUD `questanswer_*`，语料只进 **`eval_test` 测试库**。

## 为什么评测是简历核心

- 能证明「不是 demo 话术」，而是可重复实验  
- Chat 与 Eval **同编排/同检索语义**，避免两套逻辑  
- 强制你说清采样、干扰文档、策略名，防止虚高

## 指标怎么讲

| 指标 | 含义（口述） |
|---|---|
| **Hit@K** | 金标文档是否出现在前 K |
| **MRR** | 第一个正确文档排名的倒数平均 |
| **策略** | Hybrid / BM25 / …（写在结果上） |
| **N** | 用例数（优先随机 50/100，勿无脑全选） |

示例表述：

> Hybrid，N=50，Hit@5=…，MRR=…；同设置下 BM25 为 …。在 1-doc 匹配任务上两者接近，我不宣称 Hybrid 显著更优。

## 数据与库约定

1. CRUD 金标：`questanswer_*` 字段；**不要**只用 `80000_docs` 当 QA  
2. 路径约定见 `CLAUDE.md` / `docs/08`  
3. 导入目标库：**code=`eval_test`，名「测试库」**  
4. 干扰文档建议 ≥200，避免小库虚高  
5. 索引应在后台排队，导入接口不阻塞到按钮一直转圈

## 关键代码 / 页面

| 点 | 位置 |
|---|---|
| Eval 服务/runner | `backend/app/services/eval_service.py`、`backend/app/evals/` |
| 评估页 | 前端 eval feature（看板聚焦 Hit@K/MRR） |
| 策略文档 | `docs/04`、`docs/08` |

## 面试避坑

| 坑 | 正确说法 |
|---|---|
| 业务库灌金标 | 只用 eval_test |
| 只报 100% Hit@1 | 交代 N、干扰文档、是否 1-doc 任务 |
| 说 Hybrid 全面碾压 | 看曲线；无区分度就老实说接近 |
| 把 RAGAS 当主指标 | RAGAS 可选；主指标仍是 Hit@K/MRR |
| 把 memory salience 当检索分 | 两套分数，勿混淆 |

## 演示路径（评估中心）

1. CRUD 导入 → Demo-S / 随机 50  
2. 跑 Hybrid vs BM25  
3. 看板：Hit@1/5/10、MRR、策略、N  
4. 可选 local rerank → trace 有 method 名  
5. 导出 JSON/CSV  

## 相关

- 检索实现 → [03](../03-rag-retrieval/README.md)
- 诚实边界 → [09](../09-honesty-boundaries/README.md)
