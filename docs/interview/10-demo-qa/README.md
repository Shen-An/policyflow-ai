# 10. 演示路径与高频 Q&A

## A. 12 分钟演示顺序

### 1) 架构一句话（1 min）

打开 `docs/08` 或本库 [02-architecture](../02-architecture/README.md)：

- 不是 CrewAI 群聊  
- Retrieval = Service  
- Answer = 主 agent  

### 2) 在线问答（4 min）

1. 登录 → 制度问答  
2. **「差旅申请流程有哪些步骤？」**  
   - diagnostics：`need_skill=true`  
   - Skill → Answer；清单 + `[n]`  
3. 无命中胡话  
   - hard refuse  
   - `NO_RELIABLE_EVIDENCE`  
4. 可选：短跟进「给我模板」  
   - query rewrite + 历史；基于证据出填写结构  

### 3) Tool / MCP（3 min）

- diagnostics 无假 `skill.suggest`  
- MCP stdio demo health：`echo` / `time_now`  
- mock 连接器：`status=mock`  

### 4) Eval（3 min）

- CRUD → eval_test  
- 随机 50：Hybrid vs BM25  
- 看板 Hit@1/5/10、MRR、策略、N  
- 导出  

### 5) 记忆（穿插 1 min）

- `/memory` 看偏好  
- 口述：非权威 + 冷热装配 + 排序公式一句话  

---

## B. 高频追问速答

### 架构

**Q: 和 LangChain / CrewAI 项目有何不同？**  
A: 我收敛成可审计 pipeline + 证据绑定拒答 + 同路径评测；不为 multi-agent 而 multi-agent。

**Q: Agent 有几个？**  
A: 编排上多个 stage；**主 agent 是 Answer**。Memory/Retrieval 是 load/search 组件。

### RAG

**Q: Hybrid 怎么融？**  
A: 多路候选 + 融合（如 RRF）+ 可选本地 lexical rerank；以评测数字为准。

**Q: 为什么还要 BM25？**  
A: 条款号、专名、制度固定表述词法强；与语义路互补。

### 记忆

**Q: 上下文爆了怎么办？**  
A: 近窗保留；窗外摘要；事件进 LTM；召回 top-k；固定偏好 always-on。

**Q: 如何防止记忆污染制度？**  
A: prompt 分区非权威；preference 禁政策事实；无证据 hard refuse；Compliance 告警。

**Q: 冷热数据怎么存？**  
A: 同一套表；冷热是装配。窗外 raw 仍在 messages。

### Eval

**Q: 指标为什么信？**  
A: 金标 CRUD、专用测试库、干扰文档、随机 N、策略名写进结果；Chat/Eval 同检索语义。

**Q: 有没有刷分？**  
A: 禁止业务库灌金标；不单报无干扰小样本 100%。

### 工程

**Q: 为什么 SQLite？**  
A: 实习/面试 MVP 可单机跑通；模型层可迁 PG；向量规模边界我清楚。

**Q: 如何保证可演示稳定？**  
A: 分阶段测试、注入 Fake LLM/检索适配器、文档化启动与彩排清单。

---

## C. 开场 30 秒 + 收尾 20 秒

**开场**见 [01-overview](../01-overview/README.md)。

**收尾：**

> 项目价值在于把企业制度问答做成 **可编排、可拒答、可评测、可演示** 的诚实系统。  
> 我清楚本地 rerank、mock MCP、记忆装配的边界，并在仓库文档里写死，方便复现而不是只存在于口述。

---

## D. 面试前命令

```bash
conda activate policyflow
# 或使用本机 policyflow 环境 Python
pytest tests/test_memory_system.py tests/test_phase3_skill_draft_mcp_memory.py tests/test_phase2_rag_chat.py -q
# 启动
python start.py
# 或 uvicorn backend.app.main:app --reload
```

完整彩排：`docs/09-interview-demo-script.md`、`scripts/rehearse_interview_checklist.py`。  
