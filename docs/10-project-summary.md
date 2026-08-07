# 10. 项目总结（Project Summary）

版本：v1.0
日期：2026-07-26
用途：项目的**单页权威快照**——定位、架构、功能、指标、工程质量、诚实边界。新读者从这里进入；细节以各专项文档为准。

> 面试口径请配合 [docs/interview/](interview/README.md)（分章知识库）与 [09-interview-demo-script.md](09-interview-demo-script.md)（现场演示脚本）。

---

## 1. 一句话定位

**PolicyFlow AI = 企业内部政策问答与流程助手**：统一编排的 **tool-using RAG** 对话系统，Skill / Tool / MCP 分层诚实实现，检索效果用 CRUD 金标 + Hit@K / MRR 量化。**不是**关键词 if 拼成的假 multi-agent 壳。

解决四件事：

1. **可追溯**——回答绑定本轮检索证据，无可靠证据硬拒答；
2. **结构化**——流程类问题由 Skill 在证据上合成清单/对比/模板，无证据不编；
3. **多轮不断线**——四层记忆 + query rewrite，处理「给我模板」类短跟进；
4. **可量化**——专用评测库 + Hit@1/5/10 + MRR，写清检索策略与样本量。

## 2. 技术栈

| 层 | 选型 |
|---|---|
| Backend | FastAPI + SQLModel + SQLAlchemy + SQLite（WAL，可迁移 PostgreSQL） |
| RAG | Hybrid（LightRAG 路径 + BM25，RRF 融合）+ 本地 lexical fusion rerank（非 cross-encoder） |
| AI 编排 | `AgentPipeline` 单编排；Answer 为主 agent，带 tool loop |
| Memory | 四层（L0 消息 / L1 近窗+滚动摘要 / L2 事件向量摘要 / L3 实体），本地排序公式 |
| Frontend | React 19 + Vite + antd 6 + TanStack Query + 自定义设计 token（浅/深双主题） |
| 交互 | Chat 走 SSE（`POST /api/chat/stream`），分阶段流式（记忆加载/改写/检索/回答/回写） |
| 测试 | pytest（后端 134）+ vitest（前端 108）+ Playwright e2e（f0–f7） |
| 环境 | conda `policyflow`，Python 3.11+；`python start.py` 一键启动 |

## 3. 架构总览

```text
React SPA（工作台/聊天/知识库/评估/管理 16 路由，全部懒加载）
   │  REST + SSE
FastAPI routers（15 个路由模块，薄壳）
   │
services 层（chat / kb / document / eval / memory / audit / …）
   │
AgentPipeline（Chat 与 Eval 同一编排，禁止双写 stage）
   ├─ Router：结构化路由 + 难度分级（CoT / ToT 按难度选路径）
   ├─ Plan：L1/L2 渐进计划，支持并行 wave 与用户选路
   ├─ Retrieval Service：Hybrid（LightRAG + BM25 + RRF）→ 可选本地 rerank
   ├─ Skill（证据绑定业务规程）/ Tool（可审计原子能力）/ MCP（stdio/http 真协议，企业连接器 mock 且标注 status=mock）
   ├─ Answer Agent：tool loop + 证据绑定生成
   ├─ Critique→Improve 反思闭环 + 质量门（无证据 → 硬拒答）
   └─ TurnState 黑板：集中状态与错误
   │
Memory 子系统（load → 使用 → writeback；记忆非权威，不覆盖本轮 RAG 证据）
   │
SQLite（25+ 表，全 FK/状态列索引；WAL + busy_timeout；物理删除含关联清理）
```

关键事实：

- **检索是 Service 不是伪 Agent**；主 agent 只有 Answer + tool loop。
- **diagnostics 全部真实**：只报真实 tool 调用，无伪造 trace。
- 评测语料只进专用库（code=`eval_test`「测试库」），业务库不灌金标。

## 4. 功能清单（按页面）

| 页面 | 能力 |
|---|---|
| 工作台 | 统计总览、快捷入口、最近活动、服务健康 |
| 制度问答（Chat） | SSE 流式 + 阶段时间线、引用溯源可点、计划路径选择、反馈（有用/无用/引用错误）、会话管理（重命名/删除/搜索）、Markdown 渲染与复制/编辑 |
| 我的草稿 | 政策草案创建/编辑/确认/导出 Markdown |
| 我的记忆 | 四层记忆管理（仅本人），类型过滤、删除 |
| 知识库 | 多库管理、文档上传/重索引/物理删除、后台索引队列、ACL 权限 |
| FAQ 审核 | 高频问答沉淀审核（通过/驳回） |
| 评估中心 | CRUD 金标导入（含干扰文档）、随机 50/100 采样、多策略 Run（Hybrid/BM25/…）、Hit@1/5/10 + MRR 看板、逐条结果折叠、JSON/CSV 导出 |
| 审计日志 | Tool 调用与请求审计，敏感信息脱敏 |
| Skill / MCP / 模型设置 / 用户管理 | 管理面（sys_admin），MCP 连通性健康检查、Chat 与 Embedding 独立服务配置 |

## 5. 评估体系

- **主指标**：Hit@1 / Hit@5 / Hit@10 / MRR，报告**必须写清检索策略与 N**。
- **金标**：CRUD 数据集 `questanswer_*` split；导入支持 ≥200 干扰文档，避免小库虚高。
- **结论表述纪律**：1-doc 整篇匹配任务上 Hybrid 与 BM25 接近是常见现象，无区分度时不宣称「Hybrid 显著更优」。
- 公式实现：`backend/app/evals/retrieval_metrics.py`（有单测）；RAGAS 为可选路径。

## 6. 工程质量现状（2026-07-26）

- **测试**：后端 pytest 134/134；前端 vitest 108/108（37 文件）；Playwright e2e 8 条（f0 冒烟 → f7 无障碍 axe）。
- **类型与风格**：`tsc -b` 通过；ruff + mypy(strict) 已配置；eslint 存量 12 项（react-hooks 效应类，无功能影响）。
- **性能与健壮性**（本轮优化落地）：
  - SQLite 开启 WAL / `synchronous=NORMAL` / `busy_timeout=5000` / 外键强制；
  - `list_conversations` 关键词过滤、分页、计数全部下推 SQL（原双重 N+1）；
  - 启动期历史回填迁移改为 `app_backfill_migrations` 标记表**只跑一次**（原每次启动全表扫描 ToolCallLog）；
  - 前端 `MutationCache.onError` 全局兜底 + antd message 桥接，44 处写操作失败不再静默；自带内联错误 UI 的 mutation 用 `selfHandledMutation` meta 跳过，避免双重提示；
  - 删除 30 个未使用 shadcn 组件与 8 个死依赖（node_modules -251 包）。
- **已知待办**（识别未做，见 [08 §1.2](08-de-toy-multiagent-skill-eval-strategy.md)）：LLM/embedding 调用的 `httpx.AsyncClient` 连接复用；`routes_chat` 少数 `async def` 端点持同步 Session；BM25 每查询全量加载语料的指纹优化。

## 7. UI / 设计系统

- 设计方向：冷灰画布 + 白色浮卡 + 克制青绿主色（`#0f9a74`），浅色侧栏；深色模式全量适配（CSS 变量驱动）。
- Token 单一来源：`frontend/src/styles/tokens.css` + `palette.ts` + `antd-theme.ts`。
- 交互语言：卡片 hover 上浮/按压、快捷入口箭头滑动、SSE 阶段 quiet chips、`prefers-reduced-motion` 降级。
- 工作台/登录页 2026-07-26 重设计：大字号问候 + 日期行、重排统计瓷贴、点阵登录画布。

## 8. 诚实边界（简历/面试红线）

- Rerank 是**本地 lexical fusion**，不是 cross-encoder，不夸大。
- MCP 企业连接器是 **mock**，响应带 `status=mock`；协议层（stdio/http）是真的。
- LightRAG score 有合成衰减成分，评测不当作模型相关分。
- 记忆**非权威**，不能覆盖本轮 RAG 证据；偏好不写入制度条款措辞。
- 无可靠证据默认硬拒答；off-topic 检索结果不当制度依据。
- 只报 Hit@K/MRR 时必须交代 N、干扰文档、是否 1-doc 任务。

## 9. 里程碑（git 主线）

| 阶段 | 内容 |
|---|---|
| Phase 0–5 | FastAPI 骨架 → 知识库/文档 → RAG 检索 → Chat/引用 → 评估中心 → 验收（`docs/07`） |
| 去玩具化改造 | 统一 `AgentPipeline`、真 tool loop、CRUD 金标导入、Hit@K/MRR 看板（`docs/08`） |
| 记忆与多轮 | 四层记忆、query rewrite、SSE 阶段流、记忆管理页 |
| Agent 深化 | L1/L2 渐进计划 + 并行 wave、CoT/ToT 按难度、TurnState 黑板、Critique→Improve 反思闭环 |
| 体验与质量 | 深色模式、quiet chips、限流硬化；SQLite WAL、N+1 修复、全局错误提示、工作台/登录重设计（2026-07-26） |

## 10. 文档导航

| 文档 | 内容 |
|---|---|
| [01-architecture-design.md](01-architecture-design.md) | 架构设计 |
| [02-database-design-sqlite.md](02-database-design-sqlite.md) | 数据库设计 |
| [03-api-design.md](03-api-design.md) | API 设计 |
| [04-ai-pipeline-rag-eval-design.md](04-ai-pipeline-rag-eval-design.md) | AI/RAG/Eval 设计 |
| [05-development-roadmap.md](05-development-roadmap.md) | 路线图 |
| [06-frontend-implementation-design.md](06-frontend-implementation-design.md) | 前端实现 |
| [07-phase5-acceptance.md](07-phase5-acceptance.md) | Phase 5 验收 |
| [08-de-toy-multiagent-skill-eval-strategy.md](08-de-toy-multiagent-skill-eval-strategy.md) | **去玩具化总策略（实现以此为准，§10 落地状态）** |
| [09-interview-demo-script.md](09-interview-demo-script.md) | 面试演示脚本 |
| [interview/](interview/README.md) | 面试知识库（11 章） |
