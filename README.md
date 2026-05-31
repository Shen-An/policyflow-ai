# PolicyFlow AI

企业内部政策问答与流程助手。基于 **FastAPI + SQLModel + SQLite** 后端，以及 **React + Ant Design** 前端，内嵌 **HKUDS LightRAG** 完成授权知识库检索与制度问答。

一个进程、一个端口同时提供页面与 API，适合实习 / MVP 快速演示与本地部署。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **制度问答** | 多知识库联邦检索、引用溯源、可信度与合规提示；无可靠证据时明确标注「模型参考 · 不可信」 |
| **历史会话** | 按用户隔离的会话列表、搜索、重命名、删除 |
| **知识库** | 授权可见；文档上传 / 索引 / 详情正文预览；支持 TXT、Markdown、DOCX、文本型 PDF |
| **草稿** | 邮件、清单、申请、FAQ 等草稿的创建、编辑、确认、导出 |
| **FAQ 审核** | 草稿生成、人工通过 / 驳回，通过后增量入库索引 |
| **评估中心** | 回答 / 检索用例、评估 Run、检索调试 |
| **Skill / Tool / MCP** | Skill 证据规程执行、Tool 调用审计（敏感字段脱敏）、MCP 真协议客户端（stdio/http）+ 企业连接器可 mock 标注 |
| **模型设置** | Chat 与 Embedding 独立配置 OpenAI 兼容接口，密钥加密存储 |
| **用户与审计** | 用户 / 角色管理，系统审计日志 |

---

## 技术栈

- **后端**：Python 3.11+、FastAPI、SQLModel / SQLAlchemy、SQLite  
- **前端**：React 19、TypeScript、Vite、Ant Design 5、TanStack Query  
- **RAG**：进程内 LightRAG（无需单独 LightRAG 服务）  
- **鉴权**：JWT Bearer Token  

---

## 一键启动

### 环境要求

- conda 环境 `policyflow`（或任意 Python 3.11+）  
- Node.js / npm（用于构建前端）  

### 安装依赖

```powershell
conda activate policyflow
cd E:\Coding\Code\Python\policyflow-ai
pip install -e ".[dev]"
cd frontend
npm install
cd ..
```

### 配置环境变量

复制根目录 `.env.example` 为 `.env`，至少配置：

```env
SECRET_KEY=请换成足够长的随机密钥
BOOTSTRAP_ADMIN_PASSWORD=首次启动时创建管理员的密码
```

### 启动

在项目根目录：

```powershell
# 推荐：双击 start.bat，或
.\start.bat

# 或
python start.py
```

启动器会：

1. 检查并按需构建前端  
2. 启动统一服务（默认 `8000`）  
3. 自动打开浏览器  

访问地址：

```text
http://127.0.0.1:8000
```

### 常用启动参数

```powershell
.\start.bat --dev          # 开发模式：FastAPI + Vite 热更新
.\start.bat --no-browser   # 不自动打开浏览器
.\start.bat --rebuild      # 强制重新构建前端
.\start.bat --port 8080    # 修改端口
```

健康检查：

```text
GET /health
```

---

## 默认管理员

首次启动且数据库中尚无该用户时，会根据环境变量创建引导管理员：

| 项 | 默认值 |
|----|--------|
| 用户名 | `admin`（`BOOTSTRAP_ADMIN_USERNAME`） |
| 邮箱 | `admin@example.com` |
| 密码 | `.env` 中的 `BOOTSTRAP_ADMIN_PASSWORD` |

初始化幂等：账号已存在则不会重复创建。

---

## 使用前建议配置

1. **登录** 管理员账号  
2. 打开 **模型设置**，分别配置：  
   - **Chat**：OpenAI 兼容对话接口（支持 Chat Completions / Responses）  
   - **Embedding**：OpenAI 兼容向量接口（索引与检索必需）  
3. 在 **知识库** 中上传制度文档并等待索引完成  
4. 进入 **制度问答** 提问  

说明：

- 配置变更一般**无需重启后端**即可在下次请求生效  
- **更换 Embedding 模型或向量维度后，需要重新索引文档**  
- Embedding 连接失败会自动重试 2～3 次，并返回更明确的中文错误  

---

## 目录结构

```text
policyflow-ai/
├── backend/app/          # FastAPI 应用（API、服务、RAG、Agent）
├── frontend/             # React + Ant Design 前端
├── docs/                 # 架构、数据库、API、RAG/Eval、前端设计文档
├── scripts/              # 重索引、种子文档等脚本
├── tests/                # 后端与契约测试
├── start.py / start.bat  # 一键启动
├── pyproject.toml
└── README.md
```

---

## 核心能力说明

### 制度问答与检索

- 按用户 ACL 过滤可访问知识库  
- 联邦检索 + 跨库重排  
- 支持检索模式：`naive` / `local` / `global` / `hybrid` / `mix`  
- 回答附带引用、可信度、合规结果  
- **无可靠制度证据**时：可返回模型参考回答，但前端会标注 **不可信，需人工判断**  

### 历史会话（用户隔离）

- 列表、关键词搜索、重命名、删除  
- 仅当前用户可见自己的会话  
- 系统管理员不在列表中浏览他人历史（按 ID 查看等管理能力另议）  

### 知识库与文档

- 支持类型：`txt`、`md`、`docx`、文本型 `pdf`  
- 上传目录：`UPLOAD_DIR`（默认 `uploads/`）  
- LightRAG 工作区：`RAG_WORKSPACE_DIR`（默认 `rag_workspaces/`），**按知识库隔离**  
- 文档详情可查看解析后的正文内容  

### 草稿 / Skill / MCP

- 草稿生命周期：创建 → 编辑 → 确认 / 丢弃 → 导出 Markdown  
- Skill 基于证据执行（清单/对比/摘要）；Tool 调用写审计日志并脱敏  
- MCP：stdio/http 走真实 JSON-RPC；企业 SaaS 可 mock，响应带 `status=mock`  

### 评估与 FAQ

- FAQ 草稿生成与人工审核，通过后增量索引  
- CRUD 语料导入、Hit@K/MRR/HitAll、多策略对比、JSON/CSV 导出  
- 可选本地 lexical rerank（非 cross-encoder）；RAGAS 默认关，开启失败时 reason 可见  

---

## 主要 API（节选）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录 |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/chat` | 制度问答 |
| GET | `/api/conversations` | 历史会话列表（用户隔离） |
| GET | `/api/conversations/{id}` | 会话详情 |
| PATCH | `/api/conversations/{id}` | 重命名 |
| DELETE | `/api/conversations/{id}` | 删除 |
| GET/POST | `/api/knowledge-bases...` | 知识库与文档 |
| GET | `/api/documents/{id}` | 文档详情（含正文） |
| GET/POST | `/api/drafts...` | 草稿 |
| GET/POST | `/api/faq-drafts...` | FAQ 审核 |
| GET/POST | `/api/eval/...` | 评估与检索调试 |
| GET/PUT | `/api/settings/model-providers/{chat\|embedding}` | 模型设置 |

统一错误响应包含 `request_id`；HTTP 响应头含 `X-Request-ID`、`X-Process-Time-Ms`。

更完整的接口说明见：`docs/03-api-design.md`。

---

## 开发与测试

```powershell
conda activate policyflow
pip install -e ".[dev]"

# 后端测试
pytest

# 前端开发
cd frontend
npm install
npm run dev

# 前端类型检查 / 单测
npm run build
npm test
```

生产启动前会校验前端产物（无源码 map、无 MSW、无测试账号泄漏等）。

---

## 常用运维脚本

```powershell
# 将文档重建进进程内 LightRAG 工作区
python scripts/reindex_lightrag.py

# 写入偏正式的企业示例文档（需服务已配置且账号可用）
python scripts/seed_enterprise_docs.py
```

---

## 设计文档

| 文档 | 内容 |
|------|------|
| [docs/01-architecture-design.md](docs/01-architecture-design.md) | 架构设计 |
| [docs/02-database-design-sqlite.md](docs/02-database-design-sqlite.md) | 数据库设计 |
| [docs/03-api-design.md](docs/03-api-design.md) | API 设计 |
| [docs/04-ai-pipeline-rag-eval-design.md](docs/04-ai-pipeline-rag-eval-design.md) | AI / RAG / Eval |
| [docs/05-development-roadmap.md](docs/05-development-roadmap.md) | 开发路线图 |
| [docs/08-de-toy-multiagent-skill-eval-strategy.md](docs/08-de-toy-multiagent-skill-eval-strategy.md) | **去玩具化 / 多智能体落点 / Skill·Tool·MCP 诚实实现 / CRUD Eval（Hit@K·MRR）总策略** |
| [docs/09-interview-demo-script.md](docs/09-interview-demo-script.md) | 面试演示脚本（12 分钟路径 + 问答对齐代码） |
| [docs/frontend/](docs/frontend/) | 前端范围、路由权限、交付与部署 |

---

## 角色权限（简要）

| 角色 | 能力 |
|------|------|
| `employee` | 问答、草稿、已授权知识库 |
| `kb_admin` | 知识库维护、FAQ 审核、评估 |
| `sys_admin` | 用户、审计、Skill/MCP、模型设置等系统管理 |

---

## 注意事项

1. **密钥**：生产环境务必更换 `SECRET_KEY` 与管理员密码。  
2. **Embedding 稳定性**：外网向量服务偶发连接失败时会自动重试；仍失败请检查网络 / 代理 / API Key。  
3. **前端超时**：通用请求默认 60s，制度问答单独 180s，避免多库检索被过早中断。  
4. **懒加载资源**：前端生产构建带 hash；更新后若出现 chunk 加载失败，请强制刷新（Ctrl+F5）。  
5. **对话记忆**：当前问答**不会把完整历史轮次拼进模型上下文**；会话主要用于展示与管理，权威答案仍以知识库检索证据为准。  

---

## 许可证

MIT
