# UI-REVIEW — PolicyFlow AI 前端

审查方法：以 `ui-ux-pro-max` 技能生成的「企业政策助手 / 数据密集型后台」设计系统为基准，对 `frontend/src` 现有实现做六维度回溯审查。每条发现均给出 `file:line` 证据与修复方向。

> 结论先行：前端基础扎实、可访问性 genuinely 良好（skip-link、aria-live、role=status/alert、全局 focus-visible、prefers-reduced-motion、表单标签齐全）。本次需要做的是**一致性与完整性收敛**，而非重做 UI——与 `FRONTEND-CLAUDE-PLAN.md` 的判断一致。无 BLOCK 级阻断问题。

## 设计系统基准（来自 ui-ux-pro-max）

| 维度 | 基准推荐 | 现状对照 |
|---|---|---|
| 风格 | Data-Dense Dashboard（数据密集、网格、最小留白） | ✅ 符合 |
| 主色 | Indigo/Blue 系，保守、高完整性 | ✅ `--color-primary: #2563eb` |
| 强调/CTA | Emerald | ⚠️ success 用 `#15803d`，但软底色全靠字面量 `bg-emerald-50/700` |
| 图标 | SVG（Lucide/Heroicons），禁用 emoji | ✅ 全程 Lucide，无 emoji |
| 触控目标 | ≥44×44px | ⚠️ Button `min-h-10`(40)、输入 `min-h-9`(36) 偏小 |
| 对比度 | 文本 ≥4.5:1 | ✅ 基本达标（见 A11y 段） |

## 六维度评分（1–4，4 最佳）

| 维度 | 分 | 说明 |
|---|---|---|
| 1. 视觉层级与布局 | 3 | 标题/卡片/表格层级清晰，页面结构一致 |
| 2. 颜色与字体 | 2 | token 存在但被广泛绕过；软状态色全靠字面量；无暗色 |
| 3. 间距与一致性 | 2 | space token 存在，但 app-shell 混用字面量；按钮无 variant |
| 4. 组件与模式 | 2 | Button 无 variant；Loading/Empty/Error 每页各造一份 |
| 5. 响应式与交互 | 3 | 移动侧栏、表格 overflow-x、防抖搜索、在线状态齐全；触控目标偏小 |
| 6. 可访问性 | 3 | skip-link / aria-live / focus / reduced-motion / 标签齐全；少量缺口 |

---

## HIGH 优先级

### H1 · Button 缺少 variant，次级样式被复制 22 次
- **证据**：`components/ui/button.tsx:7` 仅有单一 primary 样式。次级/ghost 按钮样式 `bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50` 在 **14 个文件、22 处**手写重复（`app-shell.tsx:116`、`users-page.tsx:65`×2、`document-list-page.tsx:113,120,175`、`chat-page.tsx:116`、`draft-list-page.tsx`、`draft-detail-page.tsx`、`knowledge-base-list-page.tsx`、`knowledge-base-detail-page.tsx`、`integrations-page.tsx` 等）。
- **影响**：任何次级按钮的视觉调整需改 22 处；已出现漂移迹象（部分 `hover:bg-slate-50`，部分可能不一致）。违反 DRY 与一致性。
- **修复**：为 `Button` 增加 `variant: 'primary' | 'secondary' | 'ghost' | 'danger'`，把上述字面量收敛进组件；全量替换调用点。验收：`grep` 该字面量串应返回 0。

### H2 · 无共享状态组件，Loading/Empty/Error 每页各造
- **证据**：`users-page.tsx:74-76` 定义了具名的 `LoadingState/EmptyState/ErrorState`，但 `grep "function (LoadingState|EmptyState|ErrorState)"` 全仓**仅此 1 个文件**有。其余 11+ 页面（`chat-page.tsx:130-174`、`document-list-page.tsx:62-83`、`draft-list-page.tsx`、`knowledge-base-list-page.tsx`、`faq-review-page.tsx`、`evaluation-page.tsx`、`skills-page.tsx`、`integrations-page.tsx`、`model-settings-page.tsx`）各自内联，且标记不一致：
  - loading 高度：users `min-h-64`、documents `min-h-48`、chat 无最小高度
  - empty：users 无边框、documents `border-dashed`、chat 居中无框
  - 标题级别：users `h3`、documents `h4`、chat `h3`
  - 错误重试：users/documents/chat 均有重试按钮，但实现各异
- **影响**：状态体验随页面漂移；新增页面需复制粘贴；难以统一改版。
- **修复**：在 `components/feedback/` 新增 `state-views.tsx`，导出 `<LoadingState/>`、`<EmptyState icon title hint/>`、`<ErrorState error onRetry/>`，统一最小高度、图标、标题级别与重试按钮；各页面替换。`full-page-loading.tsx` / `full-page-error.tsx` 已有的全屏版本保留用于路由级。验收：页面内不再内联 `border-red-200 bg-red-50` 错误块（见 H3 计数）。

### H3 · 错误横幅标记重复 13 处、无 `<Alert>` 组件
- **证据**：`grep "border-red-200 bg-red-50"` 命中 **13 个文件**。每个错误展示都手写 `<div role="alert" className="... border-red-200 bg-red-50 ...">`，颜色字面量未走 token（`--color-danger` 存在但无 `--color-danger-bg/border` 软色 token）。
- **影响**：与 H2 同源；危险色软底无法通过 token 统一调整。
- **修复**：新增 `<Alert tone="danger|warning|success|info">` 组件，配合新增 `--color-danger-50/200`、`--color-warning-50/200`、`--color-success-50/700`、`--color-primary-50/700` 软色 token；替换 13 处。

### H4 · app-shell 绕过 token 且 spacing 单位混用
- **证据**：`app-shell.tsx:75-97` 侧栏使用字面量 `bg-slate-950 text-white`、`border-slate-800`、`bg-blue-600`、`text-slate-300 hover:bg-slate-900`，未走任何 token（`--color-primary` 恰好等于 `blue-600`，语义对但硬编码）。更严重的是相邻导航链接 spacing 单位不一致：第 82–85、91 行用 `var(--space-3)`/`var(--space-2)`，第 86–90、92–94 行用字面量 `mt-2 px-3 py-2`。侧栏标题派生是 8 层嵌套三元（第 36–54 行）。
- **影响**：侧栏配色与全局 token 脱钩；间距已可见微小不齐；标题逻辑难维护。
- **修复**：① 新增侧栏 token（`--color-sidebar-bg/border/active/text/text-muted`，值映射到现有 slate 色阶或改用 primary 系）；侧栏改用 token。② 统一所有导航链接为同一 className（建议抽 `navLink(active)` 辅助函数或 `<NavLink>` + className 回调）。③ 标题派生改为路径前缀 → 标题的 `Map` 查表。

---

## MED 优先级

### M1 · 缺少软状态色 token
- **证据**：`tokens.css:1-30` 仅定义了 `--color-primary/success/warning/danger` 的主色，无配套 50/200/700 软底。`bg-blue-50 text-blue-700`（11 文件）、`bg-amber-50 border-amber-200`（`chat-page.tsx:295`）、`bg-red-50 border-red-200`（13 文件）、`text-emerald-700`（`chat-page.tsx:314,422`）全为字面量。
- **修复**：在 `tokens.css` 增补 `--color-primary-50/700`、`--color-success-50/700`、`--color-warning-50/200`、`--color-danger-50/200`；配合 H3 的 `<Alert>` 与徽章统一替换。

### M2 · 触控目标偏小
- **证据**：`button.tsx:12` `min-h-10`(40px)；`login-page.tsx:53,58`、`chat-page.tsx:254` 等输入 `min-h-10`(40)；`chat-page.tsx:402,415` 评价区 `min-h-9`(36px)。低于 44×44 触控最小值（CRITICAL 级 UX 规则）。
- **影响**：移动端登录、聊天发送、反馈提交误触概率上升。桌面后台场景影响较小。
- **修复**：将 `Button` 默认 `min-h-11`(44px)；输入类统一 `min-h-11`；保留 `min-h-9` 仅用于行内紧凑控件并配套增大点击区。验收：移动端 `e2e/f7-accessibility` 不回归。

### M3 · Chat 状态展示与全局模式不一致
- **证据**：`chat-page.tsx:131` 会话加载仅纯文本「正在加载会话…」无 spinner/skeleton；第 160 行发送中也是纯文本横幅。对比 `users-page.tsx:74` 有 `RefreshCw` 旋转图标。
- **修复**：复用 H2 的 `<LoadingState/>`（带 spinner）；发送中改用带 spinner 的内联状态，与全局一致。

### M4 · Query Mode 下拉显示原始枚举
- **证据**：`chat-page.tsx:256` `<option>` 直接渲染 `hybrid/mix/local/global/naive` 原始值，无中文标签、无说明。
- **影响**：普通用户无法理解检索模式含义。
- **修复**：建立 `queryModeLabels: Record<QueryMode, {label, hint}>` 映射，渲染中文标签；可选加 `aria-describedby` 说明。

### M5 · 部分错误态缺统一重试入口
- **证据**：`chat-page.tsx:219-222` 知识库侧栏加载失败仅一行红字「知识库加载失败」，无重试按钮，而同页会话错误（第 136 行）有重试。需逐页核对所有 `role="alert"` 是否都提供重试或可恢复操作。
- **修复**：H2 的 `<ErrorState onRetry/>` 落地后，统一要求列表类错误必须带 `refetch` 重试。

---

## LOW 优先级

- **L1 · 无暗色模式**：`tokens.css:2` `color-scheme: light`，无 dark 变量。当前 MVP 范围可接受，但 token 未按语义分层，未来加暗色成本高。仅记录。
- **L2 · 分页仅上/下一页**（`users-page.tsx:65`、`document-list-page.tsx:111`）：无页码跳转。MVP 可接受。
- **L3 · app-shell 标题派生为 8 层嵌套三元**（`app-shell.tsx:36-54`）：可读性差，随 H4 一并改为 Map 查表。
- **L4 · 状态点用字面字符 `●`**（`users-page.tsx:79`）：已 `aria-hidden`，可接受；如需可换 styled `<span>`。
- **L5 · 侧栏页脚「PolicyFlow AI · F7」**（`app-shell.tsx:97`）：对用户含义不明，建议移除版本代号或改为更清晰的版权/版本文本。
- **L6 · 侧栏导航链接无显式 focus-visible 样式**：依赖全局 `:focus-visible { outline }`（`index.css:19`），实际可聚焦，但链接 hover 有底色、focus 仅 outline，视觉反馈不对称。建议补 `focus-visible` 底色与 hover 一致。

---

## 已具备、无需改动（确认项）

- ✅ 路由守卫链完整：`ProtectedRoute` 存 return-to → `PublicOnlyRoute` 读 return-to → `RoleGuard` 转 `/forbidden`（`route-guards.tsx:8-28`）。
- ✅ 状态页齐全：`forbidden-page`、`not-found-page`、`feature-unavailable-page` 均存在且语义正确（后端未开放功能显式「尚未开放」，不发请求——符合计划第 7 条）。
- ✅ 全局 `:focus-visible`、`prefers-reduced-motion`（`index.css:19-27`）。
- ✅ skip-link「跳到主要内容」+ `<main id="main-content" tabIndex={-1}>`（`app-shell.tsx:103,119`）。
- ✅ 在线状态横幅 + 移动侧栏 Escape 关闭（`app-shell.tsx:67-72,104`）。
- ✅ 表格统一 `overflow-x-auto` 包裹，窄屏可横向滚动不溢出。
- ✅ 图标全程 Lucide、无 emoji、`aria-hidden` 正确。

## 建议修复批次（供 plan-phase 拆分）

1. **批次 A — Token 与组件基座**：补软色 token（M1）、`Button` variant（H1）、`<Alert>`（H3）、`<LoadingState/EmptyState/ErrorState>`（H2）。这是后续替换的基础。
2. **批次 B — 全量替换调用点**：用新组件替换 22 处次级按钮、13 处错误横幅、11+ 处状态视图。
3. **批次 C — app-shell 收敛**：侧栏 token 化 + 导航 className 统一 + 标题 Map 化（H4、L3、L5、L6）。
4. **批次 D — Chat 与触控**：Chat 状态对齐（M3）、Query Mode 标签（M4）、触控目标 `min-h-11`（M2）、补齐重试入口（M5）。
5. **批次 E — 测试**：为新增组件补 Vitest；更新受影响快照/断言；重跑 `e2e`（F1–F7）。

## 验收命令（来自计划）
```powershell
cd frontend
npm run typecheck
npm run lint
npm test
npm run build
npm run e2e
```
