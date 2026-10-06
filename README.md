# PolicyFlow AI

企业内部政策问答与流程助手：统一编排的 **tool-using RAG** 对话系统（Skill / Tool / MCP 分层诚实实现，检索效果用 CRUD 金标 + Hit@K / MRR 量化），正在按 SDD 规格做**企业化改造**——从单进程 SQLite Web 应用，演进到 PostgreSQL + 向量库 + 对象存储 + 持久任务 + 沙箱审批 + 受控 Electron 桌面外壳的多租户架构。

> **诚实优先**：本项目的核心约定是「说到做到、边界写清」。凡本机无法真跑的部分一律显式标注 `[~]`，绝不以 mock 冒充绿灯。进度与证据以 `specs/001-enterprise-agent-refactor/tasks.md` 和 `docs/08` §10 为准。

---

## 当前状态（务必先读）

企业化改造按阶段推进，**Stage 1–7 已落地**；进度 `specs/001-enterprise-agent-refactor/tasks.md`：**112 项完成 `[X]` + 10 项部分 `[~]` + 42 项待办 `[ ]`（共 164）**。

| 阶段 | 内容 | 状态 |
|---|---|---|
| Stage 1 | 容量基线 | ✅ |
| Stage 2 | 无状态 API + PostgreSQL 16 + 多租户/RLS + 分阶段迁移 | ✅ |
| Stage 3 | 统一可信助手，Chat/Eval 同经真 LangGraph 编排 | ✅（诚实边界见 §10） |
| Stage 4 | 大规模并发：durable job、配额、SSE、恰好一次（真 PG + Redis + RabbitMQ 验证） | ✅（1000-held-open SSE 为规模压测） |
| Stage 5 | 版本化材料/证据存储：对象存储 + Milvus + saga + reconciliation | ✅（真实基础设施） |
| **Stage 6** | **安全编辑并提交（业务 MVP）**：workspace / change-set / action digest / 幂等 mock 提交 / 沙箱校验 | ✅（gVisor **运行时** `[~]`） |
| **Stage 7** | **安全桌面外壳**：Electron 最小 capability boundary | ✅（签名**打包** `[~]`） |
| Stage 8 | 专业一致的桌面工作界面（重做 chat/knowledge/workspace/approval/admin UI） | ⏳ 未开始 |
| Stage 9 | 管理员治理企业能力 | ⏳ 未开始 |
| Stage 10 | 综合验收 + 数据权威切换 + 旧路径退出 | ⏳ 未开始 |

**诚实边界（勿当作已验收）**：

1. **Stage 7 的 Electron 安全外壳是「能力边界」，不是成品 UI**。统一的桌面工作界面属 Stage 8（未做）；**当前面向最终用户的界面仍是浏览器 SPA**（`start.py`）。
2. **签名生产打包未真跑**（本机无企业代码签名证书）：签名门与 `electron-builder` 硬化已实现并逐例验证（拒绝 unsigned/placeholder、接受真实凭据），仅最终 signed-artifact 需真实证书 → T121 `[~]`。
3. **gVisor 运行时隔离未真起 pod**（本机无 K8s 集群）：沙箱 manifest 策略完备且本机路径校验真跑，但内核级隔离待集群 → T092/T098 `[~]`。
4. **生产约束只在 PostgreSQL 上存在**：dev 的 SQLite 不产生 RLS / NOT NULL / 按租户唯一，用 SQLite 验证租户隔离无效。
5. 后端部分套件需真实基础设施（PostgreSQL / Milvus / MinIO / Redis / RabbitMQ）；不可达时会干净 skip，**不要把 skip 当通过**。

---

## 两套并存的现实（请勿混为一谈）

| | 开发 / 演示默认 | 企业化目标（生产） |
|---|---|---|
| 面向用户的入口 | 浏览器 SPA，单进程单端口（`start.py`） | 受控 **Electron 桌面应用**（外壳已就绪，UI 待 Stage 8） |
| 关系库 | SQLite 单文件 | PostgreSQL 16（唯一生产 SQL 权威，RLS 强制） |
| schema 来源 | 启动 `create_all` + 原地补列 | Alembic 分阶段迁移（001–005），生产**禁止** `create_all` |
| 检索/向量 | 进程内 LightRAG + BM25 | Milvus（单 collection + 租户 partition key，Strong 一致性） |
| 文件/材料 | 本地 `uploads/` + LightRAG workspace | 版本化对象存储（MinIO，SHA-256 核验）+ saga + reconciliation |
| 任务/并发 | FastAPI BackgroundTasks | durable job 状态机 + outbox + Redis 配额 + RabbitMQ 消费者 |
| 高影响动作 | 直接执行 | 沙箱生成草稿 → 人工批准 + fresh RBAC + digest 复核 → 幂等提交 |

企业栈按阶段在**真实基础设施**上增量落地（见 `docs/08` §10 的逐项诚实状态与 `artifacts/` 原始证据）。Stage 10 之前，两套路径并存；Stage 10（T158–T160）才在零使用观察窗口后删除旧浏览器 surface。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **制度问答** | 多知识库联邦检索、引用溯源、可信度与合规提示；无可靠证据时硬拒答或明确标注「模型参考 · 不可信」 |
| **多轮记忆** | 四层记忆（消息 / 近窗+滚动摘要 / 事件向量摘要 / 实体）+ query rewrite；记忆非权威，不覆盖本轮 RAG 证据 |
| **历史会话** | 按用户隔离的会话列表、搜索、重命名、删除 |
| **知识库** | 授权可见；文档上传 / 索引 / 正文预览；TXT、Markdown、DOCX、文本型 PDF |
| **材料 / 工作区 / 审批**（企业栈） | 授权选材 → 沙箱生成变更集草稿 → 差异/版本 → 人工批准（fresh RBAC + action digest 复核）→ 幂等 mock 提交；正式政策原件永不改写 |
| **草稿 / FAQ 审核** | 草稿创建/编辑/确认/导出；FAQ 人工通过后增量入库 |
| **评估中心** | 回答/检索用例、评估 Run、CRUD 金标 Hit@K/MRR、多策略对比、负样本门控 |
| **Skill / Tool / MCP** | Skill 证据规程、Tool 调用审计（脱敏）、MCP 真协议（stdio/http）+ 企业连接器 mock（响应带 `status=mock`） |
| **模型设置 / 用户 / 审计** | Chat 与 Embedding 独立 OpenAI 兼容配置（密钥加密）、用户/角色、系统审计 |

---

## 技术栈

- **后端**：Python 3.11+、FastAPI、SQLModel / SQLAlchemy 2；dev SQLite，**生产 PostgreSQL 16**
- **RAG / 检索**：dev 进程内 LightRAG + BM25（RRF 融合）；生产 Milvus 2.5；rerank 默认本地 lexical fusion（可选真实 NVIDIA cross-encoder，opt-in 无静默回退）
- **企业基础设施**：PostgreSQL 16、Milvus、MinIO（versioned）、Redis、RabbitMQ（`infra/dev/compose.yaml`）
- **前端**：React 19、TypeScript、Vite、Ant Design、TanStack Query
- **桌面外壳**：Electron 44（contextIsolation + sandbox + 严格 CSP）、electron-builder、WDIO（E2E）
- **鉴权**：JWT Bearer Token（桌面外壳中 refresh/session 密钥仅由 main 经 OS `safeStorage` 持有）

---

## 一键启动（开发 / 演示：浏览器 SPA）

当前最快的可用路径仍是单进程 Web 应用。

```powershell
conda activate policyflow
cd E:\Coding\Code\Python\policyflow-ai
pip install -e ".[dev]"
cd frontend; npm install; cd ..
```

复制 `.env.example` 为 `.env`，至少配置：

```env
SECRET_KEY=请换成足够长的随机密钥
BOOTSTRAP_ADMIN_PASSWORD=首次启动时创建管理员的密码
```

启动（项目根目录）：

```powershell
.\start.bat            # 或 python start.py
```

启动器会按需构建前端、启动统一服务（默认 `8000`）、打开浏览器。访问 `http://127.0.0.1:8000`。

常用参数：

```powershell
.\start.bat --dev          # FastAPI + Vite 热更新
.\start.bat --no-browser   # 不打开浏览器
.\start.bat --rebuild      # 强制重建前端
.\start.bat --port 8080    # 改端口
```

> `/health` 目前只表示进程存活，**不校验数据库 schema 版本**（T034 待补）。

---

## 企业栈与桌面外壳（Stage 2–7）

### 基础设施（本地开发全栈）

```bash
docker compose -f infra/dev/compose.yaml up -d   # PostgreSQL + Milvus + MinIO + etcd + Redis
```

### PostgreSQL 分阶段迁移（生产，必须按序）

```powershell
$env:DATABASE_URL = "postgresql+psycopg://<user>:<pass>@<host>:5432/<db>"
alembic upgrade 001                           # expand：加列/建表/legacy 租户/RLS policy
python -m migrations.backfill_legacy_tenant   # backfill：可重启批量回填（ledger + 校验和）
alembic upgrade head                          # enforce：NOT NULL、按租户唯一、FORCE RLS（迁移链已至 005）
```

- **直接 `alembic upgrade head` 会失败，这是设计**：enforce 先核对回填账本，未回填即拒绝。
- 多租户：每个受保护 repository 调用显式传 `tenant_id`；跨租户访问与「不存在」返回同一错误（防枚举）；RLS 为纵深防御。

### Electron 安全桌面外壳（Stage 7）

```bash
npm --prefix frontend run electron:build     # 构建受控 renderer + main/preload
npm --prefix frontend run test:electron      # WDIO 安全套件（真实 Electron）
npm --prefix frontend run electron:package   # 生产打包：先过签名门，缺真实证书会被正确拒绝
```

外壳的安全边界：renderer 不拥有 Node / 任意文件 / token / raw IPC / 直接认证网络能力；受控 renderer 走私有 `app://` scheme + 严格 CSP；operation-specific + sender-origin + schema 校验的 IPC；认证 API/SSE 由 main 代理（取消 + 脱敏）；导航/新窗口/webview 全拒、外链仅 https allowlist；renderer 崩溃取消未批准的 in-flight 特权请求，服务端 run 保持权威。详见 `artifacts/electron/stage7/`。

---

## 默认管理员

首次启动且库中无该用户时，按环境变量创建引导管理员（幂等）：

| 项 | 默认值 |
|----|--------|
| 用户名 | `admin`（`BOOTSTRAP_ADMIN_USERNAME`） |
| 邮箱 | `admin@example.com` |
| 密码 | `.env` 的 `BOOTSTRAP_ADMIN_PASSWORD` |

登录后在 **模型设置** 分别配置 Chat 与 Embedding（OpenAI 兼容）；在 **知识库** 上传文档并等待索引；更换 Embedding 模型/维度后需**重新索引**。

---

## 目录结构

```text
policyflow-ai/
├── backend/app/          # FastAPI 应用：api / services / rag / agents / graph / jobs
│                         #   quota / storage / retrieval / sandbox / approvals / sse / db / auth / observability
├── frontend/
│   ├── src/              # React + Ant Design SPA（含 services/desktop-api.ts 桌面 typed 客户端）
│   ├── electron/         # Stage 7 安全外壳：main / preload / shared（capability boundary）
│   └── tests/electron/   # WDIO 安全 E2E + 确定性 stub backend
├── infra/                # dev/compose.yaml（PG+Milvus+MinIO+Redis）、k8s/sandbox-job.yaml（gVisor manifest）
├── migrations/           # Alembic 分阶段迁移（001–005）+ legacy 回填
├── docs/                 # 架构 / 数据库 / API / RAG·Eval / 去玩具化策略 / 项目总结 / 面试知识库
├── specs/                # SDD 规格：spec / plan / tasks（企业化改造进度以此为准）
├── artifacts/            # 各阶段原始证据（migration / graph / recovery / storage / security / electron）
├── tests/                # 后端 单元/契约/集成/安全/恢复/迁移/负载
├── start.py / start.bat  # 一键启动（浏览器 SPA 路径）
└── README.md
```

---

## 开发与测试

```bash
# 后端（不含压测）
pytest tests -q --ignore=tests/load

# 前端：类型检查 / 单元测试 / 生产校验构建
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build

# 前端：Electron 安全套件（真实 Electron，WDIO）
npm --prefix frontend run test:electron
```

**本会话已验证（Stage 7）**：前端 `vitest` **145 passed**、`test:electron` **14 条安全 E2E 全绿**（真实 Electron 44.3.0 / Chromium 152）、`typecheck` 全绿、新增代码 `lint` clean。各阶段后端原始证据见 `artifacts/`（如 Stage 5 `pytest-stage5.txt` 130 passed、Stage 6 `pytest-stage6.txt` 157 passed；最近一次广回归见 `docs/08` §10）。

测试边界（诚实）：

- **部分后端套件需真实基础设施**：PostgreSQL（默认 `127.0.0.1:55432`，`POLICYFLOW_TEST_DATABASE_URL` 可覆盖）、Milvus / MinIO / Redis / RabbitMQ。不可达时相关用例干净 skip，**skip ≠ pass**。
- 租户隔离用例以受限角色 `policyflow_app` 执行（`SET ROLE`）；超级用户绕过 RLS，用超级用户测 RLS 等于没测。
- `npm run lint` 另有若干 **Phase 1–6 UI 旧文件** 的 react-hooks / eslint 新规则告警（master HEAD 即如此，非 Stage 7 引入）；Stage 7 新增代码零 lint 错。

---

## 诚实边界与已知缺口汇总

1. **gVisor 运行时隔离未真起 pod**（无 K8s 集群）→ 沙箱 T092/T098 `[~]`（manifest 完备、本机路径校验真跑）。
2. **签名生产打包未真跑**（无企业证书）→ Electron T121 `[~]`（签名门与 `forceCodeSigning`+fuses 已实现并验证拒绝/接受逻辑）。
3. **Stage 4 的 1000 并发 held-open SSE** 为规模压测，受每连接 DB pool pinning 限制（功能已具备）→ T072 `[~]`。
4. **Stage 3 底层执行是 pipeline 图**（与 durable `GraphService` 并存），旧 `AgentPipeline` 第二套 stage 删除划归 Stage 9（T159，需零使用观察窗口）。
5. **桌面成品 UI 尚未完成**（Stage 8）；浏览器 SPA 仍是当前用户入口，Stage 10 才退出旧 surface。
6. `/health` 不校验 schema 版本（T034）；dev SQLite 不产生生产约束。

---

## 设计文档

| 文档 | 内容 |
|------|------|
| [specs/001-enterprise-agent-refactor/tasks.md](specs/001-enterprise-agent-refactor/tasks.md) | **企业化改造任务清单与真实进度**（112 `[X]` + 10 `[~]` + 42 `[ ]` / 164） |
| [docs/08-de-toy-multiagent-skill-eval-strategy.md](docs/08-de-toy-multiagent-skill-eval-strategy.md) | **去玩具化 / Skill·Tool·MCP 诚实实现 / CRUD Eval 总策略**；§10 为逐项落地状态（含各阶段诚实边界） |
| [docs/12-postgresql-multitenancy-design.md](docs/12-postgresql-multitenancy-design.md) | **生产数据面权威**：PostgreSQL、多租户、分阶段迁移、RLS |
| [docs/10-project-summary.md](docs/10-project-summary.md) | 项目总结快照（叙述向；进度数字以 tasks.md 为准） |
| [docs/01-architecture-design.md](docs/01-architecture-design.md) · [02](docs/02-database-design-sqlite.md) · [03](docs/03-api-design.md) · [04](docs/04-ai-pipeline-rag-eval-design.md) | 架构 / 数据库 / API / AI·RAG·Eval |
| [docs/09-interview-demo-script.md](docs/09-interview-demo-script.md) · [docs/interview/](docs/interview/) | 面试演示脚本 + 分章面试知识库（含白话术语表） |

---

## 角色权限（简要）

| 角色 | 能力 |
|------|------|
| `employee` | 问答、草稿、已授权知识库、材料选取与审批流中的发起/查看 |
| `kb_admin` | 知识库维护、FAQ 审核、评估 |
| `sys_admin` | 用户、审计、Skill/MCP、模型设置等系统管理 |

> 跨租户管理默认关闭，需显式授予并独立审计（Stage 9）。

---

## 注意事项

1. **密钥**：生产务必更换 `SECRET_KEY` 与管理员密码；桌面外壳中 token 仅由 main 持有，renderer 永不接触明文。
2. **Embedding 稳定性**：外网向量服务偶发失败会自动重试；仍失败请检查网络 / 代理 / API Key。更换 Embedding 模型或维度后需重新索引。
3. **前端超时**：通用请求默认 60s，制度问答 180s。
4. **对话记忆**：当前问答不会把完整历史轮次拼进模型上下文；权威答案以本轮知识库检索证据为准。

---

## 许可证

MIT
