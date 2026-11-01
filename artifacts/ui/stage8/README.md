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

> 2026-10-21 补：这两个原始日志此前被 `.gitignore` 的 `*.log` 吞掉、不在仓库里（clone 只能看到本文件的摘述）。`.gitignore` 已补 `!artifacts/**/*.log`，`e2e-full.log` 与 `accessibility.log` 现在随仓库一起分发，可以和上面的表逐条对照。

## 窗口 + a11y 证据
- axe 在 1024×680 / 1280×800 / 1680×1050 三档窗口对 chat 与 workspace 真实扫描，0 阻断项。
- 窗口布局断言：三档下侧栏不遮挡主内容、无横向溢出。
- 窗口截图：`screens/chat-small.png`、`screens/chat-medium.png`、`screens/chat-large.png`、`screens/workspace-medium.png`（真实 Electron 渲染）。

## 四项工程校验（不回归）
- `npm --prefix frontend run typecheck` → 绿
- `npm --prefix frontend run lint` → **0 problem**（2026-10-21 复核；本文件最初记的「13 problems / 12 err + 1 warn」是 Phase 1–6 既有债，已在当天清零：`buttonVariants` 拆去 `components/ui/button-variants.ts`、`statusTone`/`ChipTone` 拆去 `components/ui/chip-tone.ts`、`useResolvedColorMode`/`useHasMounted` 拆去 `styles/use-color-mode.ts`、effect 内 setState 改渲染期调整或 `useSyncExternalStore`）
- `npm --prefix frontend run test`（vitest）→ **48 文件 / 162 passed**（2026-10-21 复核；本文件最初记的「45 文件 / 150 passed」已过期）
- `npm --prefix frontend run test:electron:e2e` → **复跑 8 failed / 0 passed**（2026-10-21）：下面的 8/8 是 **2026-10-18 的历史真绿记录**，当前宿主环境里 Chromium 起不来（`electron.exe` 直接启动即 0x80000003，与 wdio / 本项目代码无关，详见仓库根 README「没做/没验过」第 1 条）

## 诚实边界
见 `usability-study.md`：目标员工可用性研究（SC-010–SC-012）因本机无真实被试，标 `[~]`。
automated 部分（a11y / 键盘 / 多窗口）已真跑真绿，未以模拟分数冒充。
