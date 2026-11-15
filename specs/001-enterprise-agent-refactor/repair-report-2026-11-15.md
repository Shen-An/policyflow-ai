# 上轮审查问题修复（2026-11-15）

本轮处理 [现状复核](progress-review-2026-11-15.md) 中的 R1–R5、Ruff 基线与文档口径。后端全量 **981 passed / 197 skipped / 0 failed**，前端 **167 passed / 49 files**，类型检查、ESLint、Ruff 通过。最终生产构建与默认浏览器并行 E2E **19 passed / 10 workers / 25.5s**。保留原并发和断言超时。

## 修复与覆盖

| 问题 | 实际变化 | 回归证据 |
|---|---|---|
| R1 多租户清理 | cleanup 强制接收认证派生的 tenant_id；KB、文档、金标、EvalCase、删除和统计均限定租户。文档 purge 对 FAQ 通过所属 KB 限定租户，对评测 JSON 仅改本租户；RagIndexJob 通过已授权文档 ID 关联清理，其旧表没有 tenant_id | 双租户真实 API；其他租户评测状态/JSON、FAQ、文档与本地文件保留；本租户已删除文档物理清理 |
| R2 桌面业务任务 | chat/file_workflow 注册真实 handler；身份来自 token，执行时重新验证活跃成员资格。聊天调用 web/eval 共用 AgentPipeline。文件流程冻结证据与草稿，保存只读输入、ChangeSet、审批摘要和 checkpoint，等待审批时释放 preparation job 租约 | 真实 BM25 + 离线模型完成问答；文件创建→暂停→新 consumer 批准/拒绝恢复；批准一次 mock 提交、拒绝零提交；未知 kind/伪造 actor 拒绝 |
| R3 独立 worker | CLI build_app 复用 create_app 的真实依赖组装，不启动 HTTP 或种子 lifespan。缺依赖为 terminal_failed/MISSING_DEPENDENCY；文档/Eval 只有领域记录成功才完成 durable job，领域失败不假报成功。配置 broker 后 document/eval submit 只写持久意图，由 relay/consumer 接管 | 使用 CLI build_app 创建独立运行时，执行实际 BM25 检索评测并保存 EvalResult；裸 consumer 缺依赖失败。CLI 评测 N=1 仅验证接线，不作为检索质量指标 |
| R4 SSE/审批契约 | Electron 支持 CRLF/LF 分帧、跨 chunk UTF-8、event/id、多行 data；保存每个 run 的恢复游标，重连携带 Last-Event-ID。识别 run.succeeded/run.cancelled；审批发送实际 expected_version | fixture 由 backend _frame + ServerSentEvent.encode 生成，Python 校验字节一致，TS 以 7 字节分片读取；真实 backend SSE、游标恢复、取消与审批版本测试 |
| R5 聊天与并行质量门 | 首答导航时保留消息，避免历史请求清空已完成答案；用户问题先提交 L0 再加载记忆，释放 SQLite 写事务。LangGraph 启动预热，独立调用冷加载走线程池。上传/索引/Chat/Eval/Tool/Skill 的同步数据库写入不再堵事件循环 | 聊天单测恢复；独立 SQLite writer 在 memory load 前可写入；生产浏览器默认 10 workers 并行通过 |

终态快照与终态 milestone 同事务保存；审批决定与恢复任务/outbox 同事务保存，并通过 AgentRun 版本竞争取消操作。错误、取消、无证据结束不再让工作区无限加载，mock 回执明确显示“模拟提交完成”。草稿准备重试复用既有 workspace/ChangeSet/审批，避免重新创建审批目标。最终构建的 axe 扫描还修复了聊天检索模式的控件标签和主按钮文字对比度；后续 Chat/proxy 单测 15 passed，默认并行无障碍 E2E 通过。

## 验证命令与结果

本机解释器：`E:/Coding/Anaconda/envs/policyflow/python.exe`；Python 运行目录为项目根，npm/Playwright 运行目录为 frontend。

| 命令 | 结果 | 原始记录 |
|---|---|---|
| `python -m pytest -q -ra --tb=short` | 981 passed / 197 skipped，307.45s | [pytest-final.txt](../../artifacts/review/2026-11-15/pytest-final.txt) |
| `python -m ruff check backend/app --output-format concise` | 通过 | [ruff-final.txt](../../artifacts/review/2026-11-15/ruff-final.txt) |
| `npm run typecheck` | 通过 | [typecheck-final.txt](../../artifacts/review/2026-11-15/typecheck-final.txt) |
| `npm run lint` | 通过 | [lint-frontend-final.txt](../../artifacts/review/2026-11-15/lint-frontend-final.txt) |
| `npm test` | 49 files / 167 passed | [vitest-final.txt](../../artifacts/review/2026-11-15/vitest-final.txt) |
| `npm run e2e:production`（设置 POLICYFLOW_PYTHON） | 构建通过；19 passed / 10 workers，29.9s | [production-final-2.txt](../../artifacts/review/2026-11-15/production-final-2.txt) |
| `npm run build`；`npx playwright test --config=playwright.production.config.ts` | 取消界面最终修订后：构建通过；19 passed / 10 workers，25.5s | [build-final.txt](../../artifacts/review/2026-11-15/build-final.txt)、[production-final-3.txt](../../artifacts/review/2026-11-15/production-final-3.txt) |
| `npx vitest run src/features/workspace/workflow-page.test.tsx` | 3 passed；取消后没有待审批按钮 | [workflow-terminal-tests.txt](../../artifacts/review/2026-11-15/workflow-terminal-tests.txt) |

前几次失败诊断日志保留在同一目录，不能作为最终验收结果。旧审查的故障复现脚本断言旧错误行为，修复后不应继续把这些断言当成功条件。测试夹具现显式注册 transport probe，保留配额/SSE/幂等断言；ToT 测试移除已经无调用者的旧导入 monkeypatch。金标补齐租户归属，未放宽隔离条件。

生产浏览器 E2E 使用真实 FastAPI、数据库和生产前端构建；LLM/LightRAG 使用该套件既有的确定性替身，覆盖应用交互与并发响应，不证明供应商质量、真实 LightRAG 索引质量或企业容量。新的业务任务测试使用实际本地 BM25 与离线模型。

## 仍未完成的交付项

- 本地文件 handler 生成**待填写 Markdown 报销草稿**，不解析票据、不修改原件；材料通过已有可用 material_version_ids 选择，桌面暂用手工 ID 输入，尚无完整材料选择器。
- 文件恢复保存的是 AgentRun checkpoint，与 Chat/Eval 共用 AgentPipeline；尚未证明目标生产 LangGraph 持久图的全部收敛要求。T053 保持部分完成。
- `ENVIRONMENT=production` 没有本文件 handler 所需的生产沙箱执行器时，明确以 MISSING_DEPENDENCY 失败；本地 SandboxRunner 不构成 gVisor 内核隔离验收。
- 外部提交为 MockReimbursementConnector，返回 `status=mock`。新 consumer 恢复测试没有宣称实际 RabbitMQ 重投递或外部系统接入通过。
- 全量的 197 skip 包括外部 PG/RLS、对象存储、broker 等依赖；真实企业栈、容量、Electron GUI 全量、签名包和目标员工可用性仍需对应环境验收。Linux prefork 与生产资源生命周期亦不在本轮本机验证范围。
- 权威 tasks.md 仍是 **164 项：145 完成、19 部分完成、0 未开始**；本轮补齐真实接线与稳定性，不把部署和迁移验收一起勾完。

后续优先补材料选择器和生产 sandbox handler，再分别交付 PG/RLS、RabbitMQ 恢复、gVisor、容量与 Electron GUI/签名证据。策略、边界与复现结果以本报告和原始日志共同为准。
