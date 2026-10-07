# Stage 8 (User Story 4B) — UI 证据集

本阶段 = Phase 8 / US4B（T123–T137）：在安全 Electron 外壳内重做统一桌面体验
（chat / knowledge / memory / workspace / approval / admin），文件流程在同一界面展示
tree / preview / version / diff / target / status。

轮次：R37（接 Phase 7 / R36）。

## Independent Test — 真实 Electron 真跑真绿

命令：`npm --prefix frontend run test:electron:e2e`
结果（见 `e2e-full.log`）：**8 passed / 8 spec（100%），33 条 E2E**，驱动真实
**Electron 44.3.0（Chromium 152）**，经确定性 stub 后端（`frontend/tests/electron/support/stub-backend.ts`）。

| Spec | 用例 | 覆盖 |
|---|---|---|
| `chat-workflow.e2e.ts` (T123) | 5 | 空状态可点示例 → 问答 → 安静折叠的 compact staged timeline → evidence → Markdown 答案+复制 → 用户消息复制/编辑 → 断线重连恢复 → grounded refusal |
| `file-approval-workflow.e2e.ts` (T124) | 4 | 授权材料选择 → draft 变更集 → file tree/preview/version/diff → approval target+exact files/hashes+side effects+expiry → approve/reject → submission result |
| `state-recovery.e2e.ts` (T125) | 5 | loading / empty / offline / recovery / permission / conflict / error 状态，均含可执行下一步 |
| `accessibility.e2e.ts` (T126) | 5 | **axe-core 真跑**（wcag2a+wcag2aa，无 serious/critical 阻断项）于 chat+workspace × small/medium/large；键盘可达 composer + 可见焦点环；landmark 语义（`nav[aria-label]` + `main`）；侧栏/内容无遮挡、无横向溢出 |
| Phase 7 安全套件（未回归） | 14 | ipc-contract(4) / navigation(5) / renderer-crash(1) / security(4) —— 严格 CSP、origin 信任、schema 校验、崩溃取消、无 node/file/token/raw-IPC |

a11y 单独跑日志见 `accessibility.log`。

## 窗口 + a11y 证据
- axe 在 1024×680 / 1280×800 / 1680×1050 三档窗口对 chat 与 workspace 真实扫描，0 阻断项。
- 窗口布局断言：三档下侧栏不遮挡主内容、无横向溢出。
- 窗口截图：`screens/chat-small.png`、`screens/chat-medium.png`、`screens/chat-large.png`、`screens/workspace-medium.png`（真实 Electron 渲染）。

## 四项工程校验（不回归）
- `npm --prefix frontend run typecheck` → 绿
- `npm --prefix frontend run lint` → 13 problems（12 err + 1 warn，全部为 Phase 1–6 既有债 `styles/theme.tsx`、`features/skills/skills-page.tsx`；本轮新代码 0 lint 错）
- `npm --prefix frontend run test`（vitest）→ 45 文件 / 150 passed（含新增 `design-system/states.test.tsx` 5 条）
- `npm --prefix frontend run test:electron:e2e` → 8/8 spec

## 诚实边界
见 `usability-study.md`：目标员工可用性研究（SC-010–SC-012）因本机无真实被试，标 `[~]`。
automated 部分（a11y / 键盘 / 多窗口）已真跑真绿，未以模拟分数冒充。
