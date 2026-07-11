# 08. 管理与质量功能实施计划

状态：F5、F6 已完成  
覆盖阶段：F5 FAQ/审计/Eval，F6 Skill/Tool/MCP

## 本文负责

- FAQ 审核；
- 审计日志；
- 评估用例、运行和调试；
- Skill、Tool 和 MCP 管理。

## 本文不负责

- 用户管理；
- 知识库基础 CRUD；
- RAG 算法实现；
- 真实外部服务写操作。

---

## 1. 进入条件

每个模块独立检查 Router、OpenAPI、权限测试、测试数据和真实联调条件。一个模块就绪不代表本文件其他模块同时就绪。

## 2. FAQ 审核

布局：待审核列表 + 审核详情。

能力：

- 按知识库和状态筛选；
- 展示问题、答案和来源；
- 通过前提示会触发增量索引；
- 驳回必须填写原因；
- 批量操作不在首版；
- 编辑能力只有 API 明确支持时才增加。

权限：`kb_admin`、`sys_admin`，同时遵守目标知识库资源权限。

## 3. 审计日志

筛选：action、target_type、actor、时间范围、分页。

列表：时间、操作者、动作、目标、IP、摘要。

详情要求：

- token、password、secret、authorization 脱敏；
- 大对象折叠；
- 可复制 request_id；
- 不默认复制全部 detail；
- 前端不把 403 映射为空数据。

## 4. 评估中心

子模块：

1. 评估用例；
2. 检索用例；
3. Run 配置；
4. Run 历史；
5. 结果详情；
6. 单次检索调试。

指标：Hit@K、MRR、Recall@K、answer accuracy、citation hit rate、no-answer refusal rate，以及可选 RAGAS。

`disabled/skipped` 不能显示成 0 分。

首版使用指标卡和表格；没有稳定历史趋势前不引入复杂图表库。

## 5. Eval Run 轮询

- 创建 Run 后进入详情；
- 按统一轮询策略查询；
- success/failed/skipped 停止；
- 展示配置快照；
- 页面关闭不影响后端任务；
- request_id 和失败摘要可查看。

## 6. 检索调试

候选表展示：

- rank；
- retriever type；
- document/chunk；
- score；
- rerank score；
- snippet。

该页面仅面向 kb/sys admin，不对普通员工开放。

## 7. Skill 管理

展示：名称、描述、状态、版本/配置摘要（若 API 提供）。

操作：启用、禁用、手动运行。

约束：

- 仅 sys_admin；
- 手动运行参数必须有明确 Schema；
- 不允许前端执行任意代码；
- 结果展示审计标识和错误；
- 破坏性状态变化二次确认。

## 8. Tool 日志

展示工具名、调用者、状态、耗时、时间和关联请求。参数与结果详情必须脱敏和折叠。

## 9. MCP 管理

展示名称、类型、启用、健康状态、工具列表和最近检查时间。

创建/编辑：

- Endpoint 格式校验；
- 敏感字段密码输入；
- 保存后不回显明文；
- 健康检查有明确结果和错误；
- MVP 明确标识 mock 集成。

不实现真实第三方系统写入。

## 10. 允许修改范围

```text
frontend/src/features/faq-review/**
frontend/src/features/audit/**
frontend/src/features/evaluation/**
frontend/src/features/skills/**
frontend/src/features/integrations/**
frontend/src/api/faq.ts
frontend/src/api/audit.ts
frontend/src/api/eval.ts
frontend/src/api/skills.ts
frontend/src/api/tools.ts
frontend/src/api/mcp.ts
相关测试
```

## 11. 完成条件

- 每个投入生产的模块独立完成真实联调；
- 敏感详情完成脱敏；
- Eval skipped/disabled 语义正确；
- FAQ 审核后状态与索引可追踪；
- 未实现模块入口关闭；
- 未扩展到真实外部系统执行。

---

## 12. F5 执行记录（2026-07-10）

状态：已完成

依赖复核：

- FAQ、Audit、Eval Router 已挂载且 OpenAPI 稳定；
- FAQ 来源、状态机、审计和索引响应已有后端测试；
- Audit 权限、分页过滤、操作者补全、递归脱敏和 request_id 已覆盖；
- Eval 异步创建、历史、轮询终态、多类型评估、权限和 skipped/failed 语义已有后端契约；
- `faq`、`audit`、`eval` API Readiness 已提升为 `implemented`。

已实现：

- `/faq-review` FAQ 列表、知识库/状态筛选、来源详情、通过和驳回；
- 审核通过后的文档索引状态追踪；
- `/admin/audit` action、target、actor、时间和分页 URL 状态；
- 审计详情、递归脱敏展示和 request_id 复制；
- `/evaluation` 回答/检索用例创建、Run 创建、历史、轮询和结果；
- 配置快照、失败摘要、completed/skipped/failed 类型状态；
- 单次检索调试候选表。

验证：

- `npm run typecheck`：通过；
- `npm run lint`：通过；
- `npm test`：29 个文件、84 个测试通过；
- `npm run build`：通过，生产包不包含 MSW 启动；
- `npm run e2e`：11 个真实浏览器测试通过；
- `python -m pytest tests/test_f5_frontend_contract.py tests/test_phase4_faq_eval.py -q`：6 个测试通过，仅有 TestClient 上游弃用警告。

范围确认：

- 未修改 `backend/**`；
- 未实现 Skill、Tool、MCP 或真实外部系统写操作；
- 未引入复杂图表库；
- 未使用生产 Mock；
- F6 已在后续独立复核并实施，未在 F5 中提前展开。

---

## 13. F6 执行记录（2026-07-10）

状态：已完成

依赖复核：

- Skill、Tool、MCP Router 已挂载，OpenAPI 与前端契约一致；
- Skill 输入 Schema、可运行状态、审计标识和权限测试已覆盖；
- Tool 日志分页、详情、关联请求、递归脱敏及安全持久化已覆盖；
- MCP 创建/编辑、Endpoint 校验、Secret 保护、健康结果、工具清单和审计已覆盖；
- `skills`、`tools`、`mcp` API Readiness 已提升为 `implemented`。

已实现：

- `/admin/skills` Skill 注册表、版本/风险/实现状态、配置摘要和启停确认；
- 基于后端 Schema 的 JSON 参数运行，不执行任意前端代码；
- Skill 运行 output、audit_id、request_id 和标准错误展示；
- Tool 日志 tool/status URL 筛选、分页、调用者、耗时、关联请求及脱敏详情；
- `/admin/integrations` MCP 创建、编辑、状态、健康检查、工具列表和最近错误；
- Mock/External 明确标识，外部模式仅保存安全配置，不执行真实第三方写入；
- sys_admin 导航和路由守卫，employee 真实浏览器访问返回无权限页面。

验证：

- `npm run typecheck`：通过；
- `npm run lint`：通过；
- `npm test`：32 个文件、91 个测试通过；
- `npm run build`：通过，生产包不包含 MSW 启动；
- `npm run e2e`：13 个真实浏览器测试通过；
- `python -m pytest tests/test_f6_frontend_contract.py tests/test_phase3_skill_draft_mcp_memory.py -q`：6 个测试通过，仅有 TestClient 上游弃用警告。

范围确认：

- 未修改 `backend/**`；
- 未实现任意代码执行或真实外部系统写操作；
- 未在浏览器持久化 Secret；
- 仅为消除已有 F3 E2E 的可访问名称歧义，将“知识库”链接定位收紧为精确匹配，未修改 F3 功能。
