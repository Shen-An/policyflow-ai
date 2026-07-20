# PolicyFlow AI

## 项目概述
Enterprise Policy Assistant — 基于 FastAPI + SQLModel + SQLite 的企业内部政策问答与流程助手。  
目标形态：可面试演示的 **tool-using RAG**（诚实 Skill / Tool / MCP + 可量化检索评测），不是假 multi-agent 壳。

## 技术栈
- **Backend**: FastAPI
- **ORM**: SQLModel + SQLAlchemy
- **Database**: SQLite（可迁移 PostgreSQL）
- **Python**: 3.11+
- **虚拟环境**: conda（`conda activate policyflow`）

## 目录结构
```
policyflow-ai/
├── backend/
│   └── app/          # FastAPI 应用
├── docs/             # 设计文档
└── tests/            # 测试
```

## 启动
```bash
conda activate policyflow
pip install -e ".[dev]"
# 推荐：项目根目录 python start.py 或 uvicorn backend.app.main:app --reload
uvicorn backend.app.main:app --reload
```

## 项目约定（2026-07 落地后）
实现 / 改 AI 与评估时遵守下列约定；细节以 docs/08、docs/09 为准。

### 架构诚实性
- Chat 与 Eval 走**同一编排**（`AgentPipeline`），不要再双写 stage。
- 检索是 **Service**，不要包装成伪 Agent；主 agent 是 Answer + tool loop。
- **禁止**伪造 diagnostics（如 `skill.suggest:*` 假 tool trace）。
- Skill = 证据绑定业务规程；无证据应 `insufficient_evidence`，不编清单。
- MCP：stdio/http 真协议；企业连接器可 mock，响应必须带 `status=mock`。
- Rerank 默认是 **local lexical fusion**，不是 cross-encoder；勿夸大。
- 无可靠证据默认 hard refuse；勿把 off-topic 检索结果当制度依据。

### 评估 / 简历指标
- **主指标**：Hit@1 / Hit@5 / Hit@10 / MRR，**必须写清检索策略**（如 Hybrid / BM25）和 N。
- CRUD 金标：`D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json` 的 `questanswer_*`；**不要**只用 `80000_docs` 当 QA。
- 评测语料只进专用库：**code=`eval_test`，名「测试库」**；禁止往 hr/finance 等业务库灌 CRUD。
- 导入应支持干扰文档（默认建议 ≥200），避免小库 + 1-doc 金标虚高 100%。
- 跑 Run 优先 **随机 50 / 100** 检索用例，不要无脑全选几百条（多策略会成倍变慢）。
- 导入时索引应在**后台**排队，勿阻塞导入接口导致按钮一直转圈。
- Hybrid 与 BM25 在 1-doc 整篇匹配任务上接近是常见现象；无区分度时不要写「Hybrid 显著更优」。
- 知识库 / 文档删除为**物理删除**（含关联与本地文件/workspace 清理意图）。

### 记忆 / 多轮 Chat（2026-07 起）
- 四层记忆：L0=`messages`；L1=最近 K 轮 + `conversations.summary` 滚动摘要；L2=`memory_items` 事件向量摘要；L3=entity upsert。记忆**非权威**，不能覆盖本轮 RAG 证据；偏好禁止写入制度条款措辞。
- 冷热是 **prompt 装配**（hot 近窗 / warm 摘要+固定偏好实体 / cold→selected top-k 召回），**不是**独立冷热存储；窗外 raw 仍在 L0。
- LTM 召回排序：`relevance × (0.55 + 0.35·importance + 0.10·recency) + access_boost`（本地公式；`MEMORY_RANK_DECAY_LAMBDA` 可调）。`salience` 同时用于 writeback 阈值与 importance。
- 低 salience `conversation_fact` 默认 TTL（`MEMORY_CONVERSATION_FACT_TTL_DAYS`）；STM 卸载事件更短 TTL；preference/entity 长期有效。
- 管理面：`GET/DELETE /api/memory` + 前端 `/memory`（仅本人）。
- 交互优先 `POST /api/chat/stream`（SSE 阶段：记忆加载 / query rewrite / 检索 / 回答 / writeback）；`POST /api/chat` 保留给非流式。
- 短跟进句（如「给我模板」）必须结合历史做 **query rewrite**，避免检索丢主题。
- 用户要模板/表单/清单：在证据上合成可填写结构，勿仅因证据无「模板」二字硬拒答。
- 前端：助手 Markdown；回答底部复制；用户气泡悬停显示下方复制/编辑；打开/刷新滚到最新；空状态可点示例问题。

### 用户协作偏好（本项目）
- 策略与可复现结论优先**写进仓库文档**，不要只留在会话里。
- 评估页默认聚焦 Hit@1/Hit@5/Hit@10/MRR；次要配置、调试、逐条结果应折叠。
- 面试叙事对齐代码，主动说清 mock / 本地 rerank / 采样规模等边界。
- 前端体验持续打磨：信息层级清晰、少遮挡、操作贴近常见聊天产品。
- 要求推 GitHub 时：在 `master` 上 commit + `git push origin HEAD`（用户已授权推送）。

## 关键文件
- `docs/05-development-roadmap.md` — 开发路线图
- `docs/01-architecture-design.md` — 架构设计
- `docs/02-database-design-sqlite.md` — 数据库设计
- `docs/03-api-design.md` — API 设计
- `docs/04-ai-pipeline-rag-eval-design.md` — AI/RAG/Eval 设计
- `docs/08-de-toy-multiagent-skill-eval-strategy.md` — **去玩具化 / 多智能体落点 / Skill·Tool·MCP 诚实实现 / CRUD Eval（Hit@K·MRR）总策略**（实现以此为准；§10 落地状态）
- `docs/09-interview-demo-script.md` — 面试演示脚本
- `docs/interview/` — **面试知识库**（总目录 + 分章：架构/RAG/记忆/Eval/诚实边界/Q&A）
- `backend/app/services/memory_service.py` / `memory_extractor.py` / `memory_window.py` / `query_rewrite.py` — 记忆与多轮检索
- `backend/app/api/routes_chat.py` / `routes_memory.py` — Chat SSE 与记忆管理 API
- `frontend/src/features/chat/` / `frontend/src/features/memory/` — 聊天与「我的记忆」UI
