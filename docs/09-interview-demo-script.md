# 09. 面试演示脚本（PolicyFlow AI）

版本：v1.0  
日期：2026-07-14  
对应策略：[`08-de-toy-multiagent-skill-eval-strategy.md`](08-de-toy-multiagent-skill-eval-strategy.md)

> 目标：10–15 分钟讲清「不是玩具壳」，并现场点出可验证证据。

---

## 1. 30 秒电梯稿

PolicyFlow 是企业内部政策问答系统。主路径是 **统一编排的 tool-using RAG agent**：Router 结构化路由 → Hybrid 检索 → 可选 Skill 规程执行 → Answer 工具环 → Verifier 质量门 → 多层记忆回写。  
Skill / Tool / MCP 分层诚实：Tool 原子可审计，Skill 业务规程，MCP 走真协议客户端（本地 stdio 真连，企业 SaaS mock 标注）。  
评估中心可导入 CRUD 金标语料，主指标 **Hit@K / MRR / HitAll@K**，RAGAS 可选，结果可导出。

---

## 2. 演示顺序（建议 12 分钟）

### A. 架构一句话（1 min）

打开 `docs/08` 拓扑图，强调：

- 不是 CrewAI 群聊  
- Retrieval 是 Service，不是伪 Agent  
- Answer 才是主 agent（function calling）

### B. 在线问答真路径（4 min）

1. 登录 → 制度问答  
2. 问：**「差旅申请流程有哪些步骤？」**  
   - diagnostics：`need_skill=true`  
   - stage 顺序：Router → Retrieval → **Skill → Answer** → Compliance  
   - 回答应体现清单结构，并带 `[n]` 引用  
3. 问一个无知识库命中的胡话问题  
   - 默认 hard refuse  
   - `compliance.passed=false`，`NO_RELIABLE_EVIDENCE`

### C. Tool / MCP 诚实实现（3 min）

1. Tool 审计页 / diagnostics 只显示真实调用（无 `skill.suggest` 假 trace）  
2. MCP：  
   - 配 `python -m backend.app.mcp.stdio_demo_server`（stdio）  
   - health-check 看到 `echo` / `time_now`  
   - 企业 mock 连接器响应含 `status: mock`

### D. Eval 页（4 min）

1. 评估中心 → **CRUD 数据集导入**  
   - task=`questanswer_1doc`，采样 30–50  
   - **目标知识库选「测试库」(code=`eval_test`)**，不要导入 HR/财务等业务库  
   - 开启索引  
2. 启动 Run：  
   - 主策略 Hybrid  
   - 对比勾选 BM25 / LightRAG  
   - 可选 **本地重排**（lexical fusion，非 cross-encoder）  
   - 可选 RAGAS  
3. 结果看板：  
   - MRR / Hit@K / **HitAll@K**  
   - strategy_comparison 表  
4. **导出 JSON/CSV**（简历附件）

---

## 3. 面试官高频问答（对齐代码）

| 问 | 答 |
|---|---|
| 是 multi-agent 吗？ | Supervisor 流水线 + tool-using 主 agent；不是角色群聊框架 |
| Skill vs Tool？ | Tool 原子 + `ToolCallLog`；Skill 是业务规程，可调 LLM/证据 |
| MCP 为何不全真？ | 协议层真（stdio/http client）；缺租户密钥的 SaaS 用 mock adapter 并标注 |
| Hit@K 怎么算？ | gold `document_id` ∩ top-k；MRR=1/first_hit；多文档另有 HitAll@K |
| confidence 可信吗？ | 不是长度公式；由证据/引用/verifier 警告估计，见 `grounding.py` |
| LightRAG 分数？ | in-process 可能是 rank decay，`metadata.score_is_synthetic=true` |
| RAGAS？ | 可选；有依赖真跑，否则 proxy 并写 `metrics_source` |
| Rerank 是模型吗？ | 默认否：`local_lexical_fusion`；metadata 可核验，不装 cross-encoder |

---

## 4. 启动与冒烟命令

```powershell
conda activate policyflow
pip install -e ".[dev]"
# 可选：pip install -e ".[ragas]"
python start.py
```

MCP 冒烟：

```powershell
$env:PYTHONPATH="."
python scripts/smoke_mcp_stdio.py
```

核心测试：

```powershell
pytest tests/test_phase2_rag_chat.py tests/test_phase3_skill_draft_mcp_memory.py tests/test_phase4_faq_eval.py tests/test_phase5_acceptance.py tests/test_ragas_router_verifier.py tests/test_skill_answer_multidoc.py -q
```

---

## 5. 诚实边界（主动说，加分）

1. Rerank 默认是 **本地 lexical fusion**（`rerank_method=local_lexical_fusion`），不是 BGE/cross-encoder  
2. SQLite + JSON embedding 适合 demo，不适合超大规模向量检索  
3. claim–evidence 当前是词重叠规则门，不是 LLM-as-judge 全文事实验证  
4. 8 万全文可扩展，但面试默认 Demo-S 采样  
5. in-process LightRAG 分数可能是 rank decay（`score_is_synthetic=true`）

---

## 6. 面试前自检（你自己先跑一遍）

> 最近一次自动彩排：`scripts/rehearse_interview_checklist.py`  
> 结果快照：`docs/rehearsal-latest.json`（2026-07-14，**22/22 PASS**）

### 环境

- [x] `conda activate policyflow` 且服务能启动  
- [x] 模型设置：Chat + Embedding 均可用  
- [x] 至少一个知识库有已索引制度文档  
- [x] CRUD 路径可访问：`D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json`  

### 功能彩排

- [x] 流程题：`差旅申请流程有哪些步骤？` → Router `need_skill=true`，Skill→Answer 顺序，回答有清单结构  
- [x] 拒答题：无命中问题 → hard refuse + `NO_RELIABLE_EVIDENCE`  
- [x] diagnostics：无 `skill.suggest` 假 trace；可见 `ToolAllowlist` / 真实 tool  
- [x] MCP：stdio demo health 出 `echo`/`time_now`；mock 响应含 `status=mock`  
- [x] Eval：导入 Demo-S（30–50）→ Hybrid vs BM25 → 看板有 MRR/Hit@K  
- [x] 可选勾本地 rerank → trace 有 `rerank_method=local_lexical_fusion`  
- [x] 导出 JSON/CSV 可下载  

### 开口前再确认

- [x] 不说「多智能体平台 / 真 cross-encoder / 已接飞书生产」  
- [x] 能指到代码：`pipeline.py`、`chat_tools.py`、`retrieval_metrics.py`、`mcp/client.py`  

### 复跑命令

```powershell
conda activate policyflow
# 确保服务在 8000
# 若 admin 密码与 .env 不一致，先重置或用正确密码改 scripts/rehearse_interview_checklist.py
$env:PYTHONPATH="."
python -u scripts/rehearse_interview_checklist.py
```

---

## 7. 一页证据清单（给面试官点）

- [ ] Chat SSE stage 真实顺序  
- [ ] ToolCallLog / 无伪造 suggested  
- [ ] Skill 无证据 `insufficient_evidence`  
- [ ] MCP stdio demo health + call  
- [ ] CRUD import → Hit@K/MRR/HitAll  
- [ ] 多策略对比表  
- [ ] 本地 rerank metadata  
- [ ] 导出 JSON/CSV  
- [ ] 无证据 hard refuse  
