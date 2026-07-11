# PolicyFlow AI

Enterprise Policy Assistant built with FastAPI, SQLModel, and SQLite.

## 一键启动

在项目根目录双击 `start.bat`，或执行：

```powershell
.\start.bat
```

启动器会自动检查并构建前端，然后由 FastAPI 在同一个 `8000` 端口提供页面和 API，并自动打开：

```text
http://127.0.0.1:8000
```

需要前端热更新时使用：

```powershell
.\start.bat --dev
```

开发模式会自动同时启动 FastAPI 和 Vite 热更新；按 `Ctrl+C` 会一起停止。后端代码变更后重新执行该命令即可。其他选项：

```powershell
.\start.bat --no-browser     # 不自动打开浏览器
.\start.bat --rebuild        # 强制重新构建前端
.\start.bat --port 8080      # 修改统一服务端口
```

也可以直接运行较短的 Python 入口：

```powershell
python start.py
```

## Development

```powershell
conda activate policyflow
pip install -e ".[dev]"
.\start.bat --dev
```

The service exposes `GET /health` for health checks. On startup it creates the
SQLite schema and idempotently inserts the Phase 0 seed data.

## Bootstrap administrator

Copy `.env.example` to `.env`, set a strong `SECRET_KEY`, and provide
`BOOTSTRAP_ADMIN_PASSWORD` before the first startup. Initialization is idempotent;
the bootstrap account is only created when it does not already exist.

Authentication endpoints:

- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET|POST /api/users` (`sys_admin`)
- `PUT /api/users/{user_id}/roles` (`sys_admin`)

## Knowledge documents

The Phase 1 API supports `txt`, `md`, `docx`, and text-based `pdf` files.
Uploads are stored under `UPLOAD_DIR`; LightRAG workspaces are reserved under
`RAG_WORKSPACE_DIR`. Uploaded documents create pending index jobs for Phase 2.

## RAG and chat

PolicyFlow embeds the official HKUDS LightRAG engine in the FastAPI process; no
separate LightRAG server or port is required. Each knowledge base uses an isolated
workspace under `RAG_WORKSPACE_DIR`. Configure independent OpenAI-compatible Chat
and Embedding providers from the Settings page. Provider changes take effect without
restarting the backend; changing the Embedding model or dimension requires reindexing.

- `POST /api/chat` runs ACL filtering, knowledge-base routing, federated LightRAG retrieval, cross-workspace reranking, answer generation, and compliance.
- `GET /api/conversations/{conversation_id}` returns persisted messages.
- `scripts/reindex_lightrag.py` rebuilds documents into the in-process LightRAG workspaces.
- LightRAG query modes `naive`, `local`, `global`, `hybrid`, and `mix` are supported.

## Skills, drafts, and MCP mock

Phase 3 adds deterministic Skill recommendations, audited Tool execution, draft
lifecycle APIs, an in-process MCP mock, and non-authoritative conversation memory.

- `GET|POST /api/skills...` manages and manually runs built-in Skills.
- `GET|POST|PUT /api/drafts...` manages user-owned drafts.
- `GET /api/tools` and `GET /api/tool-call-logs` expose Tool metadata and audits.
- `GET|POST /api/mcp/servers...` manages mock MCP configurations and health checks.

MCP servers are disabled by default. Memory and conversation history are explicitly
separated from authoritative retrieval evidence.

## FAQ and evaluation

Phase 4 adds FAQ draft generation and human review, incremental indexing for approved
FAQ entries, retrieval metrics, reproducible evaluation runs, optional RAGAS hooks,
and retrieval-debug traces.

- `/api/faq-drafts` manages generation, approval, rejection, and indexing.
- `/api/eval/cases` and `/api/eval/retrieval-items` manage evaluation datasets.
- `/api/eval/runs` stores a full configuration snapshot with every run.
- `/api/eval/retrieval-debug` exposes rank, retriever, score, snippet, and warnings.

RAGAS remains disabled by default. Disabled or missing integrations return a
`skipped` status rather than artificial zero scores.

## Knowledge-base creation metadata

Browser clients can discover valid department identifiers through
`GET /api/departments`. Knowledge-base administrators can use
`GET /api/knowledge-bases/create-options` for the formal creation metadata contract.
Individual authorized resources are available through
`GET /api/knowledge-bases/{knowledge_base_id}`.

## Chat feedback and history contract

`POST /api/query-logs/{query_log_id}/feedback` accepts `useful`, `not_useful`,
`wrong_citation`, or `incomplete`. Query owners may submit feedback; `sys_admin`
may review any query. Repeated feedback from the same user overwrites the existing
record and updates its timestamp. Every create or update writes an audit record.

Conversation assistant messages expose a stable `meta_json` contract containing
citations, `query_log_id`, confidence, query mode, router output, suggested Skills,
and compliance results. Explicit inaccessible knowledge-base IDs are filtered
before retrieval; if none remain, Chat returns the standard no-evidence response.

Drafts may reference only an existing conversation owned by the current user.
`sys_admin` may associate a draft with another user's conversation.

## Phase 5 observability and acceptance

Every HTTP response includes `X-Request-ID` and `X-Process-Time-Ms`. Clients may
send a safe `X-Request-ID` containing letters, numbers, `.`, `_`, `:`, or `-`;
otherwise the server generates a UUID. Error responses include the same identifier:

```json
{
  "success": false,
  "error": {"code": "VALIDATION_ERROR", "message": "...", "details": []},
  "request_id": "phase5.trace:request-001"
}
```

Structured request logs automatically include the request ID, method, path, status,
and duration. Audit records inherit the active request ID even when service callers
do not pass it explicitly. The Phase 5 acceptance suite covers error boundaries,
request tracing, upstream LightRAG failures, retrieval reranking, no-evidence
refusal, and the MVP response-time target.

## F6 Skill, Tool, and MCP administration

Employees may list Skills and manually run Skills that are both enabled and
implemented. Only `sys_admin` may enable or disable Skills. Skill list entries
include `input_schema`, `implemented`, and `runnable`; successful runs return an
audit ID and request ID.

Tool-call logs are recursively redacted before persistence and again before API
serialization. The log API supports filtering, pagination, and
`GET /api/tool-call-logs/{log_id}` details with request and conversation linkage.

MCP server create, update, read, and health contracts are separated. Commands and
sensitive configuration values are encrypted at rest and never returned in API
responses. `PUT` and `PATCH /api/mcp/servers/{server_id}` support editing, while
health responses expose tools, check time, and stable error information. Startup
applies idempotent SQLite column migrations and protects legacy MCP and Tool-log
data.


## Runtime model settings

System administrators can configure independent OpenAI-compatible Chat and
Embedding providers at `/admin/model-settings`. Each provider has its own Base
URL, authentication mode, encrypted API key, model, timeout, and enabled state;
the Embedding provider additionally stores its vector dimension. Changes take
effect on the next request without restarting the backend.

Chat providers support both OpenAI Chat Completions and the OpenAI Responses API. The Base URL may be a `/v1` base or a full `/chat/completions` or `/responses` endpoint. Each provider supports independent model discovery and connectivity testing. API
keys are encrypted with `SECRET_KEY` and are never returned by the API. Legacy
combined database or environment-variable configuration is split into Chat and
Embedding providers during migration.

The in-app Embedding service uses these settings. When retrieval is delegated to
an independent LightRAG REST service, that LightRAG deployment still controls its
own internal embedding runtime and must be configured consistently.
