# 03. API 详细设计

版本：v0.1  
日期：2026-07-09  
项目：Enterprise Policy Assistant  
技术基线：FastAPI + SQLite

---

## 1. API 设计原则

1. 所有接口以 `/api` 为前缀；
2. 使用 JSON 作为主要请求 / 响应格式；
3. 文件上传使用 `multipart/form-data`；
4. 认证使用 JWT Bearer Token；
5. 返回统一响应结构；
6. 错误使用统一错误码；
7. 管理类接口需要角色权限；
8. AI / Tool / MCP 调用必须写审计日志。

统一响应结构：

```json
{
  "success": true,
  "data": {},
  "message": "ok",
  "request_id": "req_xxx"
}
```

错误响应：

```json
{
  "success": false,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "没有访问该知识库的权限",
    "details": {}
  },
  "request_id": "req_xxx"
}
```

---

## 2. 认证 API

## 2.1 登录

```http
POST /api/auth/login
```

请求：

```json
{
  "username": "admin",
  "password": "password"
}
```

响应：

```json
{
  "access_token": "jwt",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": "uuid",
    "username": "admin",
    "display_name": "系统管理员",
    "roles": ["sys_admin"]
  }
}
```

## 2.2 当前用户

```http
GET /api/auth/me
```

响应：

```json
{
  "id": "uuid",
  "username": "zhangsan",
  "email": "zhangsan@example.com",
  "display_name": "张三",
  "department": {
    "id": "uuid",
    "name": "研发部"
  },
  "roles": ["employee"]
}
```

---

# 3. 用户与权限 API

## 3.1 用户列表

```http
GET /api/users?page=1&page_size=20&keyword=zhang
```

权限：`sys_admin`

## 3.2 创建用户

```http
POST /api/users
```

权限：`sys_admin`

请求：

```json
{
  "username": "lisi",
  "email": "lisi@example.com",
  "display_name": "李四",
  "password": "initial_password",
  "department_id": "uuid",
  "role_codes": ["employee"]
}
```

## 3.3 修改用户角色

```http
PUT /api/users/{user_id}/roles
```

权限：`sys_admin`

请求：

```json
{
  "role_codes": ["employee", "kb_admin"]
}
```

---

# 4. 知识库 API

## 4.1 创建知识库

```http
POST /api/knowledge-bases
```

权限：`kb_admin` / `sys_admin`

请求：

```json
{
  "name": "财务制度库",
  "code": "finance",
  "department_id": "uuid",
  "description": "报销、差旅、付款相关制度",
  "default_query_mode": "hybrid"
}
```

响应：

```json
{
  "id": "uuid",
  "name": "财务制度库",
  "code": "finance",
  "rag_workspace": "rag_workspaces/finance",
  "status": "active"
}
```

---

## 4.2 查询知识库列表

```http
GET /api/knowledge-bases
```

普通用户只返回有 read 权限的知识库。

响应：

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "财务制度库",
      "code": "finance",
      "description": "...",
      "permission": "read",
      "document_count": 12
    }
  ],
  "total": 1
}
```

---

## 4.3 上传文档

```http
POST /api/knowledge-bases/{kb_id}/documents
Content-Type: multipart/form-data
```

权限：知识库 `write` / `admin`

表单字段：

```text
file: UploadFile
title: optional string
```

响应：

```json
{
  "document_id": "uuid",
  "title": "差旅报销制度",
  "file_type": "docx",
  "index_status": "pending",
  "index_job_id": "uuid"
}
```

---

## 4.4 查询文档列表

```http
GET /api/knowledge-bases/{kb_id}/documents?page=1&page_size=20
```

响应：

```json
{
  "items": [
    {
      "id": "uuid",
      "title": "差旅报销制度",
      "file_type": "docx",
      "index_status": "indexed",
      "source_version": 1,
      "created_at": "2026-07-09T10:00:00"
    }
  ],
  "total": 1
}
```

---

## 4.5 手动触发索引

```http
POST /api/documents/{document_id}/index
```

权限：文档所属知识库 `write` / `admin`

响应：

```json
{
  "job_id": "uuid",
  "status": "pending"
}
```

---

## 4.6 查询索引状态

```http
GET /api/documents/{document_id}/status
```

响应：

```json
{
  "document_id": "uuid",
  "index_status": "indexed",
  "index_error": null,
  "latest_job": {
    "id": "uuid",
    "status": "success",
    "started_at": "...",
    "finished_at": "..."
  }
}
```

---

# 5. 聊天问答 API

## 5.1 发送问题

```http
POST /api/chat
```

请求：

```json
{
  "conversation_id": null,
  "question": "差旅住宿标准是多少？",
  "knowledge_base_ids": [],
  "enable_skill": true
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| conversation_id | 为空时创建新会话 |
| question | 当前问题 |
| knowledge_base_ids | 可选，用户指定知识库范围；仍需权限过滤 |
| enable_skill | 是否允许自动推荐 / 调用 Skill |

响应：

```json
{
  "conversation_id": "uuid",
  "message_id": "uuid",
  "answer": "根据《差旅管理制度》...",
  "citations": [
    {
      "knowledge_base_id": "uuid",
      "knowledge_base_name": "财务制度库",
      "document_id": "uuid",
      "document_title": "差旅管理制度",
      "chunk_id": "chunk_xxx",
      "snippet": "住宿标准为..."
    }
  ],
  "confidence_score": 0.86,
  "query_mode": "hybrid",
  "router_result": {
    "domain": "finance",
    "task_type": "knowledge_qa",
    "risk_level": "low"
  },
  "suggested_skills": [
    {
      "name": "process_checklist",
      "description": "生成差旅申请流程清单"
    }
  ],
  "draft": null,
  "compliance": {
    "passed": true,
    "warnings": []
  }
}
```

无证据响应：

```json
{
  "answer": "当前知识库未找到可靠依据，建议联系相关部门确认。",
  "citations": [],
  "confidence_score": 0.0,
  "compliance": {
    "passed": true,
    "warnings": ["NO_RELIABLE_EVIDENCE"]
  }
}
```

---

## 5.2 查询会话

```http
GET /api/conversations/{conversation_id}
```

响应：

```json
{
  "id": "uuid",
  "title": "差旅住宿标准咨询",
  "status": "active",
  "summary": {},
  "messages": [
    {
      "id": "uuid",
      "role": "user",
      "content": "差旅住宿标准是多少？",
      "created_at": "..."
    },
    {
      "id": "uuid",
      "role": "assistant",
      "content": "根据...",
      "meta_json": {
        "citations": []
      },
      "created_at": "..."
    }
  ]
}
```

---

## 5.3 用户记忆管理

仅当前用户可访问自己的记忆（user 级偏好/实体/长期事件 + 本人会话摘要轨迹）。

### 列表

```http
GET /api/memory?page=1&page_size=20&memory_type=user_preference&keyword=表格
```

响应：

```json
{
  "items": [
    {
      "id": "uuid",
      "owner_type": "user",
      "owner_id": "uuid",
      "memory_type": "user_preference",
      "content": "以后请用表格回答",
      "source": "summary",
      "confidence": 0.8,
      "meta_json": {},
      "has_embedding": true,
      "expires_at": null,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

### 删除

```http
DELETE /api/memory/{memory_id}
```

返回 `204 No Content`。不可删除其他用户的记忆。

---

## 5.4 问答反馈

```http
POST /api/query-logs/{query_log_id}/feedback
```

请求：

```json
{
  "rating": "useful",
  "comment": "引用准确"
}
```

`rating` 可选：

```text
useful
not_useful
wrong_citation
incomplete
```

---

# 6. 草稿 API

## 6.1 创建草稿

```http
POST /api/drafts
```

请求：

```json
{
  "conversation_id": "uuid",
  "draft_type": "email",
  "title": "差旅报销说明",
  "content": "尊敬的财务同事...",
  "source_question": "帮我写一封差旅报销说明",
  "related_sources": []
}
```

通常由 Skill 自动创建，手动接口用于编辑和测试。

---

## 6.2 查询草稿列表

```http
GET /api/drafts?status=draft&draft_type=email&page=1&page_size=20
```

普通用户只能看自己的草稿。

---

## 6.3 查看草稿

```http
GET /api/drafts/{draft_id}
```

---

## 6.4 修改草稿

```http
PUT /api/drafts/{draft_id}
```

请求：

```json
{
  "title": "更新后的标题",
  "content": "更新后的内容"
}
```

---

## 6.5 确认草稿

```http
POST /api/drafts/{draft_id}/confirm
```

只改变状态，不自动提交外部系统。

---

## 6.6 丢弃草稿

```http
POST /api/drafts/{draft_id}/discard
```

---

## 6.7 导出草稿

```http
POST /api/drafts/{draft_id}/export
```

MVP 可返回纯文本或 markdown：

```json
{
  "export_type": "markdown",
  "content": "..."
}
```

---

# 7. FAQ API

## 7.1 生成 FAQ 草稿

```http
POST /api/faq-drafts
```

权限：`kb_admin` / `sys_admin`

请求：

```json
{
  "knowledge_base_id": "uuid",
  "source_document_id": "uuid",
  "source_conversation_id": null,
  "count": 10
}
```

响应：

```json
{
  "items": [
    {
      "id": "uuid",
      "question": "差旅报销需要哪些材料？",
      "answer": "需要发票、行程单、审批记录...",
      "status": "draft"
    }
  ]
}
```

---

## 7.2 查询 FAQ 草稿

```http
GET /api/faq-drafts?knowledge_base_id=uuid&status=pending_review
```

---

## 7.3 审核通过并入库

```http
POST /api/faq-drafts/{faq_draft_id}/approve
```

权限：目标知识库 `admin` 或 `kb_admin`

处理：

```text
faq_drafts.status = approved
写入知识库文档或 FAQ 文档
创建 rag_index_job
触发 LightRAG 增量索引
```

---

## 7.4 驳回 FAQ

```http
POST /api/faq-drafts/{faq_draft_id}/reject
```

请求：

```json
{
  "reason": "答案不准确"
}
```

---

# 8. Skill API

## 8.1 查询 Skill 列表

```http
GET /api/skills
```

响应：

```json
{
  "items": [
    {
      "name": "process_checklist",
      "version": "1.0.0",
      "description": "根据企业制度生成流程清单",
      "enabled": true,
      "risk_level": "low"
    }
  ]
}
```

## 8.2 启用 Skill

```http
POST /api/skills/{name}/enable
```

权限：`sys_admin`

## 8.3 禁用 Skill

```http
POST /api/skills/{name}/disable
```

权限：`sys_admin`

## 8.4 手动执行 Skill

```http
POST /api/skills/{name}/run
```

用于调试，MVP 可选。

---

# 9. Tool / MCP API

## 9.1 查询工具列表

```http
GET /api/tools
```

权限：`sys_admin`

## 9.2 查询工具调用日志

```http
GET /api/tool-call-logs?tool_name=knowledge.search&status=success
```

权限：`kb_admin` / `sys_admin`，普通用户不可访问全局日志。

## 9.3 查询 MCP 服务

```http
GET /api/mcp/servers
```

权限：`sys_admin`

## 9.4 创建 MCP mock 服务配置

```http
POST /api/mcp/servers
```

请求：

```json
{
  "name": "mock_office_tools",
  "command": "python -m app.mcp.mock_server",
  "config": {
    "mode": "mock"
  },
  "enabled": false
}
```

## 9.5 MCP 健康检查

```http
POST /api/mcp/servers/{server_id}/health-check
```

响应：

```json
{
  "server_id": "uuid",
  "health_status": "healthy",
  "tools": [
    "mcp.email.create_draft",
    "mcp.calendar.create_event"
  ]
}
```

---

# 10. 审计日志 API

## 10.1 查询审计日志

```http
GET /api/audit-logs?action=DOCUMENT_UPLOAD&target_type=knowledge_document&page=1&page_size=20
```

权限：`sys_admin`

响应：

```json
{
  "items": [
    {
      "id": "uuid",
      "actor_id": "uuid",
      "action": "DOCUMENT_UPLOAD",
      "target_type": "knowledge_document",
      "target_id": "uuid",
      "detail": {},
      "ip_address": "127.0.0.1",
      "created_at": "..."
    }
  ],
  "total": 1
}
```

---

# 11. 评估 API

## 11.1 创建评估用例

```http
POST /api/eval/cases
```

权限：`kb_admin` / `sys_admin`

请求：

```json
{
  "question": "差旅报销需要哪些材料？",
  "category": "finance",
  "expected_answer_keywords": ["发票", "行程单", "审批"],
  "expected_source_documents": ["差旅报销制度"],
  "expected_chunk_ids": [],
  "should_answer": true
}
```

## 11.2 查询评估用例

```http
GET /api/eval/cases?category=finance
```

## 11.3 创建检索评估用例

```http
POST /api/eval/retrieval-items
```

权限：`kb_admin` / `sys_admin`

请求：

```json
{
  "eval_case_id": "uuid",
  "query": "差旅报销需要哪些材料？",
  "knowledge_base_ids": ["uuid"],
  "relevant_document_ids": ["uuid"],
  "relevant_chunk_ids": ["chunk_001", "chunk_002"],
  "relevance_judgement": {
    "chunk_001": 3,
    "chunk_002": 2
  }
}
```

说明：

```text
该接口专门服务 Hit@K、MRR、Recall@K 等检索指标。
MVP 如果拿不到稳定 chunk_id，可只传 relevant_document_ids。
```

## 11.4 查询检索评估用例

```http
GET /api/eval/retrieval-items?enabled=true
```

## 11.5 启动评估

```http
POST /api/eval/runs
```

请求：

```json
{
  "name": "MVP RAG 评估 2026-07-09",
  "case_ids": [],
  "retrieval_item_ids": [],
  "eval_types": ["retrieval", "rag_answer", "ragas"],
  "retrieval_config": {
    "strategy": "hybrid_lightrag_bm25",
    "top_k_values": [1, 3, 5, 10],
    "rerank_enabled": false
  },
  "ragas_config": {
    "enabled": false,
    "metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
  }
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| eval_types | `retrieval` 计算 Hit@K / MRR；`rag_answer` 评估答案；`ragas` 调用 RAGAS |
| strategy | `lightrag_only` / `bm25_only` / `hybrid_lightrag_bm25` |
| top_k_values | 需要计算的 K 值 |
| rerank_enabled | 是否启用 rerank |

## 11.6 查询评估结果

```http
GET /api/eval/runs/{run_id}
```

响应：

```json
{
  "id": "uuid",
  "name": "MVP RAG 评估 2026-07-09",
  "status": "success",
  "metrics": {
    "answer_accuracy": 0.82,
    "citation_hit_rate": 0.76,
    "no_answer_refusal_rate": 0.9,
    "hit_at_1": 0.52,
    "hit_at_3": 0.74,
    "hit_at_5": 0.81,
    "mrr": 0.63,
    "ragas_faithfulness": 0.80,
    "ragas_answer_relevancy": 0.77,
    "avg_latency_ms": 8300
  },
  "results": []
}
```

## 11.7 单次检索调试

```http
POST /api/eval/retrieval-debug
```

权限：`kb_admin` / `sys_admin`

请求：

```json
{
  "query": "差旅报销需要哪些材料？",
  "knowledge_base_ids": ["uuid"],
  "strategy": "hybrid_lightrag_bm25",
  "top_k": 10,
  "rerank_enabled": true
}
```

响应：

```json
{
  "query": "差旅报销需要哪些材料？",
  "strategy": "hybrid_lightrag_bm25",
  "items": [
    {
      "rank": 1,
      "retriever_type": "bm25",
      "document_id": "uuid",
      "chunk_id": "chunk_001",
      "score": 12.4,
      "rerank_score": 0.91,
      "snippet": "报销材料包括发票、行程单..."
    }
  ]
}
```

该接口用于开发阶段观察 LightRAG、BM25、Hybrid、Rerank 的候选结果差异。

---

# 12. API 权限矩阵

| API | employee | kb_admin | sys_admin |
|---|---:|---:|---:|
| `/api/chat` | ✅ | ✅ | ✅ |
| `/api/knowledge-bases` GET | ✅ | ✅ | ✅ |
| 创建知识库 | ❌ | ✅ | ✅ |
| 上传文档 | ❌ | ✅ | ✅ |
| 草稿 CRUD | 自己 | 自己 | 全部 |
| FAQ 生成 | ❌ | ✅ | ✅ |
| FAQ 审核 | ❌ | ✅ | ✅ |
| Skill 启停 | ❌ | ❌ | ✅ |
| MCP 配置 | ❌ | ❌ | ✅ |
| 审计日志 | ❌ | 部分 | ✅ |
| Eval 管理 | ❌ | ✅ | ✅ |

---

# 13. 错误码建议

```text
AUTH_INVALID_CREDENTIALS
AUTH_TOKEN_EXPIRED
PERMISSION_DENIED
KB_NOT_FOUND
KB_ACCESS_DENIED
DOCUMENT_NOT_FOUND
DOCUMENT_TYPE_NOT_SUPPORTED
RAG_INDEX_FAILED
RAG_NO_EVIDENCE
LLM_PROVIDER_ERROR
SKILL_NOT_FOUND
SKILL_DISABLED
TOOL_NOT_FOUND
TOOL_PERMISSION_DENIED
MCP_SERVER_DISABLED
VALIDATION_ERROR
INTERNAL_ERROR
```
