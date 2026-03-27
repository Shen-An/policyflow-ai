# 07. 核心业务功能实施计划

状态：F3、F4 已完成  
覆盖阶段：F3 知识库与文档、F4 问答与草稿

## 本文负责

- 知识库和文档；
- 上传、索引状态；
- 问答、会话、引用和反馈；
- 草稿生命周期。

## 本文不负责

- FAQ、Eval、Skill、MCP；
- 后端 RAG 和 Agent 实现；
- SSE，除非另有独立变更任务。

---

## 1. 进入条件

F3/F4 只有满足以下条件才能从 Blocked 变为进行中：

- 对应 Router 已挂载；
- OpenAPI Schema 稳定；
- 权限和主要错误有后端测试；
- 前端可获得可重复的测试数据；
- API Readiness 更新为 `implemented`。

## 2. 知识库列表

能力：

- 展示名称、编码、描述、资源权限、文档数和状态；
- 普通用户只看到可读资源；
- 管理员按契约显示创建入口；
- `default_query_mode` 使用枚举；
- 筛选和分页同步 URL。

禁止根据前端角色自行过滤后伪装成安全控制。

## 3. 知识库详情

Tab：概览、文档。权限管理只有后端正式 API 存在后才增加。

操作：

- read：查看；
- write/admin：上传和重新索引；
- admin：配置操作按 API 开放。

## 4. 文档上传

- multipart/form-data；
- 单文件 MVP；
- 类型与后端 document loader 一致；
- 默认前端提示 20MB，最终以后端配置为准；
- 文件名仅展示，不作为可信路径；
- 客户端校验不替代后端校验。

状态机：

```text
selected -> uploading -> pending -> indexing -> indexed
              |                        |
              -> upload_failed         -> index_failed -> retrying
```

轮询使用 `05-api-state-error-contracts.md` 的统一策略。

## 5. 问答页

布局：消息区 + 可折叠引用详情；小屏单列。

核心组件：

- `MessageList`；
- `QuestionComposer`；
- `KnowledgeBaseSelector`；
- `AnswerCard`；
- `CitationList/Drawer`；
- `ConfidenceIndicator`；
- `SuggestedSkillCard`；
- `FeedbackActions`；
- `NoEvidenceState`。

发送流程：

1. 校验问题；
2. 插入用户消息；
3. `POST /api/chat`；
4. 用真实响应写入会话、答案、引用、合规和 Skill 建议；
5. 失败保留问题并提供重试；
6. 无证据使用独立状态，不伪造引用。

首版普通 HTTP。SSE 必须另立任务并只替换 transport adapter。

## 6. 引用

每条至少展示：

- 知识库；
- 文档标题；
- snippet；
- 可用时展示 chunk 标识。

引用为空且 confidence 为 0 时使用 No Evidence，不显示“低可信引用”。

## 7. 会话与反馈

- `GET /api/conversations/{id}` 加载历史；
- 只有所有者或管理员可访问；
- 反馈需要 `query_log_id`；
- 若 Chat 响应不返回 `query_log_id`，登记后端契约缺口，不在前端猜测；
- feedback rating 使用固定枚举。

## 8. 草稿

列表：status、draft_type、page、page_size。

详情：

- 标题和正文编辑；
- 来源问题和引用只读；
- 保存、确认、丢弃、导出；
- 未保存离开确认；
- confirm 后默认只读；
- discard 二次确认；
- 导出文件名过滤非法字符。

不执行真实外部系统提交。

## 9. 允许修改范围

```text
frontend/src/features/knowledge-bases/**
frontend/src/features/documents/**
frontend/src/features/chat/**
frontend/src/features/drafts/**
frontend/src/api/knowledge-bases.ts
frontend/src/api/chat.ts
frontend/src/api/drafts.ts
相关通用组件和测试（需说明复用依据）
```

## 10. 关键测试

- 知识库 read/write/admin 操作差异；
- 上传类型、大小和错误；
- 索引轮询停止；
- 问答成功、失败、无证据；
- 引用展开和键盘操作；
- 反馈缺少 query_log_id 的阻塞行为；
- 草稿未保存、确认、丢弃和导出。

## 11. 完成条件

- 所有功能真实联调；
- 权限来自后端；
- 引用和无证据语义正确；
- 长任务无无限轮询；
- 不涉及 FAQ/Eval/MCP 范围。


---

## 12. F3 执行记录（2026-07-10）

状态：已完成

依赖复核：

- KB/Document Router 已挂载，OpenAPI 包含列表、创建、详情、创建元数据、文档上传/列表、状态和重新索引接口；
- `GET /api/departments` 与 `GET /api/knowledge-bases/create-options` 提供可重复的部门元数据；
- 后端权限、404/422、上传、索引与 ACL 契约测试通过；
- `knowledgeBases` 与 `documents` API Readiness 已提升为 `implemented`。

已实现：

- ACL 过滤后的知识库列表、详情概览和文档 Tab；
- 管理员/知识库管理员创建知识库，包含部门和检索模式选择、编码冲突处理；
- 文档列表、TXT/Markdown/DOCX/PDF 与 20MB 客户端校验、后端上传错误映射；
- pending/indexing 状态轮询，30 秒后退避，并在 indexed/failed 时停止；
- write/admin 可上传和重试失败索引，read 仅查看；
- 搜索、筛选和分页状态同步 URL，不复制服务端状态到全局 Store。

验证：

- `npm run typecheck`：通过；
- `npm run lint`：通过；
- `npm test`：18 个文件、61 个测试通过；
- `npm run build`：通过，生产包不包含 MSW 启动；
- `npm run e2e`：9 个真实浏览器测试通过；
- `python -m pytest tests/test_kb_frontend_contract.py tests/test_phase1_knowledge.py -q`：3 个测试通过。

范围确认：

- 未修改 `backend/**`；
- 未实现 Chat、Draft、FAQ、Eval、Skill、MCP 或 SSE；
- 未使用生产 Mock；
- F3 完成记录生成时，F4 仍保持阻塞，等待 Chat/Conversation/Feedback/Draft API 独立就绪性复核。

---

## 13. F4 执行记录（2026-07-10）

状态：已完成

依赖复核：

- Chat、Conversation、Feedback、Draft Router 已挂载；
- Chat 响应和历史消息均返回稳定的 `query_log_id`、引用、可信度、检索模式、路由结果、Skill 建议与合规元数据；
- Feedback 固定枚举、所有权、管理员例外、覆盖语义与审计已有后端契约测试；
- Draft 会话归属、所有权、状态转换、分页和错误行为已有后端测试；
- `chat`、`feedback`、`drafts` API Readiness 已提升为 `implemented`。

已实现：

- `/chat` 与 `/chat/:conversationId` 制度问答和历史恢复；
- 授权知识库选择、检索模式、发送防重复、失败保留问题与重试；
- 引用展开、可信度、合规状态、Skill 建议和标准无证据状态；
- 固定 rating 反馈、可选备注、重复提交覆盖提示；
- `/drafts` URL 筛选/分页和草稿创建；
- `/drafts/:draftId` 编辑、保存、未保存离开保护、确认只读、丢弃确认与安全文件名 Markdown 导出。

验证：

- `npm run typecheck`：通过；
- `npm run lint`：通过；
- `npm test`：24 个文件、76 个测试通过；
- `npm run build`：通过，生产包不包含 MSW 启动；
- `npm run e2e`：10 个真实浏览器测试通过；
- `python -m pytest tests/test_f4_frontend_contract.py tests/test_phase2_rag_chat.py tests/test_phase3_skill_draft_mcp_memory.py -q`：8 个测试通过，仅有 TestClient 上游弃用警告。

范围确认：

- 未修改 `backend/**`；
- 未实现 SSE、FAQ、Audit、Eval、Skill 管理或 MCP；
- 未使用生产 Mock；
- F5 需独立复核 FAQ/Audit/Eval API 后再启动。
