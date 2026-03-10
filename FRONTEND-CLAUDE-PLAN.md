# Claude 前端修改计划

## 建议调用的 Skill

首选：`$gsd-ui-review`

原因：当前前端已经完成主要页面和 API 接入，不适合重新做 UI 方案；应先对现有实现进行视觉、交互、响应式、无障碍和一致性审查，再按审查结果修改。

修改流程：

```text
$gsd-ui-review
→ $gsd-plan-phase
→ $gsd-execute-phase
→ $gsd-verify-work
```

如果只想让 Claude 直接完成一次集中修改，可使用：

```text
$gsd-audit-fix
```

## 修改范围

仅修改 `frontend/`，不要修改 `backend/` 和后端 API 契约。

重点检查并修改：

1. 统一页面布局、间距、颜色、字体、按钮、表单、表格和弹窗样式。
2. 完善移动端与窄屏响应式布局，避免侧栏、表格和操作区溢出。
3. 完善 loading、empty、error、forbidden、not-found 和 feature-unavailable 状态。
4. 检查登录态、401 跳转、角色权限菜单和受保护路由。
5. 改善 Chat、知识库、文档、草稿、FAQ、评估和管理页面的操作反馈。
6. 补齐键盘操作、焦点样式、ARIA、表单标签和颜色对比度。
7. 删除仅用于占位或调试的 UI；后端不支持的功能必须明确显示“暂不可用”。
8. 保持现有 API 封装、React Query、路由懒加载和权限模型，不进行无关重构。
9. 为修改内容补充或更新 Vitest 与 Playwright 测试。

## 验收命令

```powershell
cd frontend
npm run typecheck
npm run lint
npm test
npm run build
npm run e2e
```

当前已确认 `typecheck`、`lint`、93 个单元测试和生产构建通过；`e2e` 需要 Claude 修改后重新完整执行。

## 给 Claude 的执行要求

- 先运行 `$gsd-ui-review`，基于生成的 `UI-REVIEW.md` 确定修改项。
- 优先处理 BLOCK、高优先级和影响主要用户流程的问题。
- 每一项修改必须对应明确的问题与验收方式。
- 不改后端，不伪造 API，不用静态假数据替代真实业务接口。
- 完成后输出修改文件列表、测试结果和仍未解决的问题。
