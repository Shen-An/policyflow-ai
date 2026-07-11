# 02. SQLite 数据库详细设计

版本：v0.1  
日期：2026-07-09  
项目：Enterprise Policy Assistant  
数据库：SQLite

---

## 1. 设计原则

需求文档原始数据库设计偏 PostgreSQL。本项目详细设计阶段选择 SQLite，因此做以下适配：

| PostgreSQL 思路 | SQLite 设计 |
|---|---|
| uuid | `String(36)` 保存 UUID 字符串 |
| jsonb | SQLAlchemy `JSON` 或 `Text` 保存 JSON 字符串 |
| timestamp | `DateTime` |
| numeric | `Float` / `Numeric` |
| bool | `Boolean` |
| enum | `String` + 代码层枚举校验 |

统一字段建议：

```python
id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
created_at = Column(DateTime, default=datetime.utcnow)
updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

所有表名使用小写复数形式。

---

## 2. 表分类

### 2.1 用户与权限

```text
users
roles
user_roles
departments
```

### 2.2 知识库与文档

```text
knowledge_bases
knowledge_base_permissions
knowledge_documents
rag_index_jobs
```

### 2.3 会话与问答

```text
conversations
messages
ai_query_logs
```

### 2.4 草稿与 FAQ

```text
drafts
faq_drafts
```

### 2.5 Skill / Tool / MCP

```text
skills
tools
tool_call_logs
mcp_servers
```

### 2.6 记忆、审计、模型、评估

```text
memory_items
audit_logs
model_providers
eval_cases
eval_runs
eval_results
retrieval_eval_items
```

说明：

- `eval_cases` 用于端到端 RAG 问答评估；
- `retrieval_eval_items` 用于检索专项评估，保存 query 与标准相关文档 / chunk；
- `eval_results` 需要预留 Hit@K、MRR、RAGAS 等指标字段。

---

## 3. 用户与权限表

## 3.1 users

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| username | String(64) | 用户名，唯一 |
| email | String(255) | 邮箱，唯一 |
| password_hash | String(255) | 密码哈希 |
| display_name | String(100) | 显示名 |
| department_id | String(36), nullable | 所属部门 |
| status | String(20) | active / disabled |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

索引：

```text
unique(username)
unique(email)
index(department_id)
```

---

## 3.2 roles

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| code | String(50) | employee / kb_admin / sys_admin |
| name | String(100) | 角色名 |
| description | Text | 描述 |
| created_at | DateTime | 创建时间 |

---

## 3.3 user_roles

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | String(36) | 用户 ID |
| role_id | String(36) | 角色 ID |

主键：

```text
(user_id, role_id)
```

---

## 3.4 departments

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(100) | 部门名 |
| code | String(50) | 部门编码 |
| parent_id | String(36), nullable | 上级部门 |
| created_at | DateTime | 创建时间 |

---

# 4. 知识库与文档表

## 4.1 knowledge_bases

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(100) | 知识库名称 |
| code | String(50) | hr / finance / it / admin / legal |
| department_id | String(36) | 所属部门 |
| description | Text | 描述 |
| rag_workspace | String(255) | LightRAG workspace 路径或名称 |
| default_query_mode | String(20) | naive / local / global / hybrid / mix |
| status | String(20) | active / archived |
| created_by | String(36) | 创建人 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

索引：

```text
unique(code)
index(department_id)
index(status)
```

---

## 4.2 knowledge_base_permissions

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| knowledge_base_id | String(36) | 知识库 ID |
| subject_type | String(20) | user / role / department |
| subject_id | String(36) | 主体 ID |
| permission | String(20) | read / write / admin |
| created_at | DateTime | 创建时间 |

索引：

```text
index(knowledge_base_id)
index(subject_type, subject_id)
```

---

## 4.3 knowledge_documents

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| knowledge_base_id | String(36) | 知识库 ID |
| title | String(255) | 文档标题 |
| file_path | String(500) | 原始文件路径 |
| file_type | String(20) | txt / md / docx / pdf |
| content_text | Text, nullable | 抽取文本 |
| content_hash | String(128) | 内容 hash |
| index_status | String(20) | pending / indexing / indexed / failed / deleted |
| index_error | Text, nullable | 索引错误 |
| source_version | Integer | 文档版本 |
| created_by | String(36) | 上传人 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

索引：

```text
index(knowledge_base_id)
index(index_status)
index(content_hash)
```

---

## 4.4 rag_index_jobs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| knowledge_document_id | String(36) | 文档 ID |
| job_type | String(20) | insert / reindex / delete |
| status | String(20) | pending / running / success / failed |
| started_at | DateTime, nullable | 开始时间 |
| finished_at | DateTime, nullable | 结束时间 |
| error_message | Text, nullable | 错误信息 |
| retry_count | Integer | 重试次数 |
| created_at | DateTime | 创建时间 |

---

# 5. 会话与问答表

## 5.1 conversations

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| user_id | String(36) | 用户 ID |
| title | String(255) | 会话标题 |
| channel | String(20) | web / api / mcp |
| status | String(20) | active / archived |
| summary | Text, nullable | 结构化摘要 JSON 字符串 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

---

## 5.2 messages

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| conversation_id | String(36) | 会话 ID |
| role | String(20) | user / assistant / tool / system |
| content | Text | 消息内容 |
| meta_json | JSON | 附加信息，如 citations、tool_use、token_usage |
| created_at | DateTime | 创建时间 |

注意：字段名建议用 `meta_json`，避免和 SQLAlchemy `metadata` 保留名冲突。

---

## 5.3 ai_query_logs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| conversation_id | String(36) | 会话 ID |
| user_id | String(36) | 用户 ID |
| question | Text | 问题 |
| answer | Text | 答案 |
| knowledge_base_ids | JSON | 使用的知识库 ID 列表 |
| retrieved_sources | JSON | 检索来源 |
| confidence_score | Float | 置信度 |
| query_mode | String(20) | LightRAG query mode |
| latency_ms | Integer | 耗时 |
| token_usage | JSON | token 统计 |
| created_at | DateTime | 创建时间 |

---

# 6. 草稿与 FAQ 表

## 6.1 drafts

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| user_id | String(36) | 用户 ID |
| conversation_id | String(36) | 会话 ID |
| draft_type | String(30) | email / checklist / application / faq / help_request / summary |
| title | String(255) | 草稿标题 |
| content | Text | 草稿内容 |
| source_question | Text | 来源问题 |
| related_sources | JSON | 引用来源 |
| status | String(20) | draft / confirmed / discarded / exported |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

---

## 6.2 faq_drafts

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| knowledge_base_id | String(36) | 目标知识库 |
| source_conversation_id | String(36), nullable | 来源会话 |
| question | Text | FAQ 问题 |
| answer | Text | FAQ 答案 |
| status | String(30) | draft / pending_review / approved / rejected / indexed |
| generated_by | String(20) | ai / manual |
| reviewer_id | String(36), nullable | 审核人 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

---

# 7. Skill / Tool / MCP 表

## 7.1 skills

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(100) | Skill 名称 |
| version | String(50) | 版本 |
| description | Text | 描述 |
| config | JSON | Skill 配置 |
| enabled | Boolean | 是否启用 |
| risk_level | String(20) | low / medium / high |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

---

## 7.2 tools

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(100) | 工具名 |
| description | Text | 描述 |
| input_schema | JSON | 输入 schema |
| output_schema | JSON | 输出 schema |
| risk_level | String(20) | low / medium / high |
| enabled | Boolean | 是否启用 |
| timeout_seconds | Integer | 超时 |
| created_at | DateTime | 创建时间 |

---

## 7.3 tool_call_logs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| conversation_id | String(36), nullable | 会话 ID |
| agent_name | String(100) | Agent 名称 |
| tool_name | String(100) | 工具名称 |
| user_id | String(36), nullable | 触发用户 |
| input_summary | JSON | 输入摘要 |
| output_summary | JSON | 输出摘要 |
| status | String(20) | success / failed / denied / timeout |
| error_message | Text, nullable | 错误信息 |
| latency_ms | Integer | 耗时 |
| created_at | DateTime | 创建时间 |

---

## 7.4 mcp_servers

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(100) | 服务名 |
| command | String(500) | 启动命令或连接地址 |
| config | JSON | 配置 |
| enabled | Boolean | 是否启用 |
| health_status | String(20) | unknown / healthy / unhealthy |
| last_checked_at | DateTime, nullable | 最近检查时间 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

---

# 8. 记忆、审计、模型、评估表

## 8.1 memory_items

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| owner_type | String(20) | user / conversation / system |
| owner_id | String(36) | 所属对象 |
| memory_type | String(50) | conversation_summary / user_preference / system_note |
| content | Text | 记忆内容 |
| source | String(20) | manual / summary / tool |
| confidence | Float | 可信度 |
| expires_at | DateTime, nullable | 过期时间 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

约束：

```text
不得保存制度事实为 user_preference。
不得保存敏感个人信息。
```

---

## 8.2 audit_logs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| actor_id | String(36), nullable | 操作人 |
| action | String(100) | 操作 |
| target_type | String(100) | 目标类型 |
| target_id | String(36), nullable | 目标 ID |
| detail | JSON | 详情 |
| ip_address | String(64), nullable | IP |
| created_at | DateTime | 创建时间 |

---

## 8.3 model_providers

新增表，用于避免模型配置写死。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(100) | provider 名称 |
| provider_type | String(50) | openai_compatible / anthropic |
| base_url | String(500), nullable | API base url |
| api_key_env | String(100) | API key 环境变量名 |
| default_chat_model | String(100) | 默认聊天模型 |
| default_embedding_model | String(100), nullable | 默认 embedding 模型 |
| enabled | Boolean | 是否启用 |
| config_json | JSON | 扩展配置 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

安全要求：

```text
只保存 api_key_env，不保存真实 API key。
```

---

## 8.4 eval_cases

端到端问答评估用例，用于检查最终回答质量、引用质量和拒答质量。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| question | Text | 测试问题 |
| category | String(50) | hr / finance / it / admin / legal / no_answer |
| expected_answer_keywords | JSON | 期望关键词 |
| expected_source_documents | JSON | 期望引用文档 |
| expected_chunk_ids | JSON, nullable | 期望命中的 chunk ID，后续精细评估使用 |
| should_answer | Boolean | 是否应该回答 |
| enabled | Boolean | 是否启用 |
| created_at | DateTime | 创建时间 |

---

## 8.5 retrieval_eval_items

检索专项评估用例，用于计算 Hit@K、MRR、Recall@K 等指标。它和 `eval_cases` 可以关联，也可以独立存在。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| eval_case_id | String(36), nullable | 关联的端到端评估用例 |
| query | Text | 检索 query |
| knowledge_base_ids | JSON | 检索范围 |
| relevant_document_ids | JSON | 标准相关文档 ID 列表 |
| relevant_chunk_ids | JSON | 标准相关 chunk ID 列表；没有 chunk 时可为空 |
| relevance_judgement | JSON, nullable | 更细粒度相关性标注，如 graded relevance |
| enabled | Boolean | 是否启用 |
| created_at | DateTime | 创建时间 |

说明：

```text
Hit@K 判断 top_k 中是否命中任一 relevant_document_id 或 relevant_chunk_id。
MRR 使用第一个相关结果的 rank 计算 reciprocal rank。
如果无法稳定拿到 LightRAG chunk_id，则先按 document_id 评估，后续再升级到 chunk 级。
```

---

## 8.6 eval_runs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| name | String(255) | 评估名称 |
| status | String(20) | pending / running / success / failed |
| total_cases | Integer | 总用例数 |
| started_at | DateTime, nullable | 开始时间 |
| finished_at | DateTime, nullable | 结束时间 |
| metrics | JSON | 汇总指标 |
| created_by | String(36), nullable | 创建人 |
| created_at | DateTime | 创建时间 |

---

## 8.7 eval_results

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) | 主键 |
| eval_run_id | String(36) | 评估运行 ID |
| eval_case_id | String(36), nullable | 端到端用例 ID |
| retrieval_eval_item_id | String(36), nullable | 检索专项用例 ID |
| question | Text | 问题快照 |
| answer | Text, nullable | 回答 |
| retrieved_sources | JSON | 检索来源，含 rank、score、retriever_type |
| retrieval_metrics | JSON, nullable | Hit@K、MRR、Recall@K 等 |
| ragas_metrics | JSON, nullable | RAGAS 指标，如 faithfulness、answer_relevancy |
| score | Float | 综合得分 |
| passed | Boolean | 是否通过 |
| error_message | Text, nullable | 错误 |
| latency_ms | Integer | 耗时 |
| created_at | DateTime | 创建时间 |

`retrieval_metrics` 示例：

```json
{
  "hit_at_1": 0,
  "hit_at_3": 1,
  "hit_at_5": 1,
  "mrr": 0.5,
  "recall_at_5": 0.67,
  "first_relevant_rank": 2
}
```

`ragas_metrics` 示例：

```json
{
  "faithfulness": 0.82,
  "answer_relevancy": 0.76,
  "context_precision": 0.71,
  "context_recall": 0.68
}
```

---

# 9. 初始化数据

MVP 初始化时应创建：

## 9.1 角色

```text
employee
kb_admin
sys_admin
```

## 9.2 部门

```text
HR
Finance
IT
Admin
Legal
```

## 9.3 默认知识库

```text
hr
finance
it
admin
legal
```

## 9.4 默认 Skill

```text
knowledge_qa
process_checklist
application_draft
policy_compare
faq_generate
risk_check
summary
```

## 9.5 默认 Tool

```text
knowledge.search
knowledge.insert
knowledge.reindex
draft.create
draft.update
faq.create_draft
faq.approve
memory.read
memory.write
mcp.call
```

---

# 10. SQLite 注意事项

1. SQLite 写并发有限，MVP 可接受；
2. 索引任务不要长时间持有数据库事务；
3. 大文件内容不要全部塞数据库，原文件放 uploads；
4. `content_text` 可以保留用于调试，但大文档可只保存抽取摘要或关闭保存；
5. JSON 字段在 SQLite 中主要用于存取，不建议做复杂查询；
6. 后续迁移 PostgreSQL 时，`JSON` 可迁移为 `JSONB`。
