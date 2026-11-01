# T053 切片 2：影子对比能不能放行「把 chat 流量切到持久图」

- 日期：2026-11-01
- 触发：T053 切片 1 已落地 `ChatTurnState` + 共享节点 + 影子记录（`ChatShadowDiff@1`），切片 2 的问题只有一个——**影子图与权威路径的差异有没有清零到可以切流量**。
- 结论：**没有清零，不切流量。** `GRAPH_SHADOW_MODE` 保持默认关，chat 仍走 `AgentPipeline`（`backend/app/agents/pipeline_graph.py`）。字段级差异已经可以完整归因到影子模式的刻意降级；事件级差异只落在 stage 进度回调与 `diagnostics_partial` 的交错顺序上，尚未定量收敛。

## 怎么测的

影子跑在权威路径内部：`backend/app/graph/shadow.py` 用 `ChatShadowRunner.wrap()` 代理 `AgentPipeline.run`，把 `on_stage` / `on_event` tee 一份，权威响应原样返回给客户端，然后**用同一份输入异步再跑一次持久图**（`_ShadowPipelineShim` + `build_pipeline_graph`，`disable_side_effects=True` → `session/user/tool_executor=None`、`execute_skills=False`），逐字段比对后写 `artifacts/graph/shadow/<UTC>-<identifier>.json`。

复现命令（工作目录=仓库根，`PYTHONPATH=.`）：

```powershell
$env:GRAPH_SHADOW_MODE="1"
python -m pytest tests/contract/test_graph_shadow_mode.py `
  tests/contract/test_graph_shadow_event_labels.py `
  tests/contract/test_graph_shadow_parity_classification.py `
  tests/integration/test_graph_route_adapter_live.py -q
python scripts/_shadow_summary.py
```

实测：`13 passed in 2.74s`（`GRAPH_SHADOW_MODE=1`），`13 passed in 2.54s`（`GRAPH_SHADOW_MODE=0`）。只有 `tests/integration/test_graph_route_adapter_live.py` 打真实 `/api/chat`，所以一次跑只产生 1 份记录。

## 两种口径

`diff` 里同时给两个判定，避免为了好看而稀释严格口径：

| 键 | 含义 |
| --- | --- |
| `match` | **严格**：字段全等 + 事件序列（标签级）全等 + 影子自身 `ok` |
| `comparable_match` | **可比**：字段差异全部能由「影子刻意不执行 skill」解释，且事件序列全等 |
| `unexplained_mismatches` | 既不属于降级、也无法解释的字段 |
| `explained_by_degradation` | 字段 → 归因说明 |

本次记录：`strict=False`、`comparable=False`、`shadow ok=True`、`unexplained=[]`、`mismatches=['answer','skills']`、`events_match=False`、`first_divergence=17`。

## 字段级差异：已完全归因

`answer` 与 `skills` 两项，在 5 份历史记录上逐份都是这两项，且都是同一个原因：

- 答案正文**完全一致**（`len=45`、`sha256` 前缀相同），只有 `confidence` 差 **0.03**（权威 0.67 / 影子 0.64）。
- 差值来源已定位到 `backend/app/agents/grounding.py:258-259`：`if skill_results and any(item.get("status") == "success" for item in skill_results): base += 0.03`。影子跑 `execute_skills=False`，没有成功 skill，正好少这 0.03。
- `skills` 字段为空同理：影子不执行 skill，结果恒为空。

所以 `unexplained_mismatches` 为空不是巧合，而是被 `_explain_degradation()` 精确覆盖：只有当正文与 `tool_trace` 相同、且置信度差恰好等于 `SKILL_CONFIDENCE_BONUS = 0.03` 时才判为「降级解释」；答案正文不同、或差值不是 0.03、或 `router`/`compliance`/`errors` 不一致，一律不解释（`tests/contract/test_graph_shadow_parity_classification.py` 的 4 条反向断言钉住了这点）。

## 事件级差异：没清零

真实 chat 记录（问句「What is the travel approval process?」）的逐位对比：

- index 0–16：两侧完全相同（`stage:RouterAgent:running` → `diagnostics_partial` → `plan` → `stage:PlanExecutor:running` → `plan_step` × 2 → `stage:RetrievalAgent:success` → `plan_step` × 4 → `stage:PlanExecutor:success`）。
- index 17 起：权威是 `diagnostics_partial`，影子是 `stage:SkillAgent:success`；此后 `stage:AnswerAgent:*`、`stage:ReflectionLoop:running`、`stage:CritiqueAgent:*`、`stage:ComplianceAgent:*` 整体错位一位。
- 长度：权威 31 个事件、影子 30 个，差的正好是末尾那一个 `diagnostics_partial`。客户端可见的 `plan` / `plan_step` 事件一个不少。

结论：分歧全在**stage 进度回调与 diagnostics 更新的交错顺序**，属编排层噪声，不是答案 / 路由 / 检索内容分歧。但「噪声」不等于「已知安全」——SSE 客户端确实会看到事件顺序不同，所以切片 2 不放行。

一个方法学教训值得记下来：**只比事件名会掩盖真实差异**。裸名字层两边都以 `"stage"` 开头，LCS 只报「多了/少了一个 `diagnostics_partial`」，看起来像「只差一个事件」；把 stage 事件打成 `stage:<stage>:<status>` 标签后才看出是整体错位。因此 `event_sequence` 现在同时保留 `authoritative` / `shadow`（标签）与 `authoritative_names` / `shadow_names`（裸名），`first_divergence` / `edit_script` 都按标签算。

## 要切流量还差什么

1. **事件顺序对齐**：要么让影子也发 `diagnostics_partial`（但影子 `disable_side_effects=True`，诊断更新本身是副作用），要么在 diff 里把「stage 与 diagnostics 交错顺序」显式声明为可忽略项并说明 SSE 客户端不依赖该顺序——后者需要 SSE 契约测试支撑，不能只写一句注释。
2. **confidence 口径**：影子不执行 skill 就不该被要求给出含 skill 加分的置信度。切流量前需要决定：切过去以后 `execute_skills=True`，那时 confidence 应自然对齐；这需要在**真开 skill** 的路径上再取一轮记录，而不是继续用降级影子比对。
3. **规模**：目前一次跑只有 1 份真实 chat 记录（5 份历史记录来自多文件连跑）。切片 2 的最低证据量应是多问句 × 多入口的一批记录，而不是单条。

## 诚实边界

- 影子记录是**运行期产物**，`artifacts/graph/shadow/*.json` 不提交（每次跑 uuid/时间戳都变）；可核查的是本文 + `artifacts/acceptance/shadow-parity-summary.txt` 的汇总口径与复现命令。
- `artifacts/acceptance/shadow-parity-summary.txt` 每次按当前记录数重算，数字随跑法变化，文中引用的是「13 passed 那一跑」的 1 份记录。
- 未测：并发下的影子开销、影子跑失败对权威路径的影响（代码里异常被吞并记 `shadow.error`，但没有专门的故障注入测试）、真开 skill 时的对齐情况。
