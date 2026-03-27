# 06. 基础功能实施计划

状态：Draft  
覆盖阶段：F1 认证与应用壳、F2 用户管理  
后端依赖：当前已满足

## 本文负责

- 登录；
- 应用壳；
- 当前用户与退出；
- 用户列表、创建用户、修改角色。

## 本文不负责

- 知识库、问答、草稿；
- 用户删除、禁用、重置密码；
- 修改后端认证机制。

---

## 1. F1 登录

页面字段：用户名、密码、登录按钮、错误摘要。

流程：

1. 本地校验非空；
2. `POST /api/auth/login`；
3. 保存 Token、过期时间和返回用户；
4. 需要时调用 `/api/auth/me` 校验；
5. 跳转登录前目标地址；
6. `AUTH_INVALID_CREDENTIALS` 显示统一凭据错误；
7. 请求中禁止重复提交。

验收：

- 键盘可完成登录；
- 密码默认隐藏；
- 错误焦点可达；
- 登录成功、失败、过期均有测试。

## 2. F1 AppShell

包含：

- 桌面侧栏/移动抽屉；
- 页面标题和面包屑；
- 当前用户菜单；
- 主内容区；
- 全局网络提示；
- Toast 和确认框容器。

不包含业务仪表盘。根路由跳到用户第一个可用功能。

## 3. F1 状态页面

必须提供：

- `/forbidden`；
- 404；
- 全页加载；
- 全页错误与重试；
- API 未开放提示（仅开发或明确产品文案）。

## 4. F2 用户列表

接口：

```text
GET /api/users?page=1&page_size=20&keyword=...
```

表格字段：

- 显示名；
- 用户名；
- 邮箱；
- 部门；
- 角色；
- 状态；
- 创建时间；
- 操作。

行为：

- 关键词 300ms debounce；
- 搜索和页码同步 URL；
- 默认 20，最大 100；
- Loading、Empty、Error 分开；
- 只有 sys_admin 可访问。

## 5. F2 创建用户

字段严格对应当前 `UserCreate`：

```text
username
email
display_name
password
department_id optional
role_codes
```

前端规则：

- username 规则与后端一致；
- password 最少 8；
- 至少一个 role；
- 409 显示用户名/邮箱冲突；
- 422 映射字段；
- 成功后关闭表单并刷新列表。

不提前增加后端没有的字段。

## 6. F2 修改角色

接口：

```text
PUT /api/users/{user_id}/roles
```

要求：

- 至少保留一个角色；
- 明确展示目标用户；
- 提交中防重复；
- 成功后更新对应行；
- 不提供角色定义编辑。

## 7. 允许修改范围

```text
frontend/src/features/login/**
frontend/src/features/users/**
frontend/src/app/**
frontend/src/auth/**
frontend/src/components/layout/**
frontend/src/components/feedback/**
frontend/src/api/auth.ts
frontend/src/api/users.ts
相关测试
```

禁止创建 Chat/KB/Eval 实现。

## 8. 测试

组件：

- 登录成功/失败；
- 401 退出；
- 用户列表加载/空/错误；
- 创建用户 409/422；
- 修改角色成功和失败。

E2E：

```text
匿名深链接 -> 登录 -> 返回
sys_admin -> 用户列表 -> 创建 -> 修改角色
employee -> /admin/users -> forbidden
```

## 9. 完成条件

- F1、F2 全部使用真实后端；
- 无未实现按钮；
- 权限和错误行为明确；
- E2E 通过；
- 未扩展到其他业务模块。


---

## 10. F1 执行记录（2026-07-10）

状态：已完成

已完成：

- 真实登录、凭据错误、会话恢复、退出与 Token 过期处理；
- `sessionStorage` Token 过渡方案，未使用 `localStorage`；
- 匿名深链接登录后返回、已登录访问登录页重定向；
- `sys_admin` 与 `employee` 路由隔离，页面级与操作级 403 反馈；
- 响应式 AppShell、移动抽屉、当前用户、网络状态与反馈容器；
- 未开放业务模块不进入生产导航，也不发送设计态 API 请求。

验证：

- `npm run typecheck`、`npm run lint`、`npm test`、`npm run build` 全部通过；
- 12 个测试文件、34 个单元/组件测试通过；
- 7 个 Chromium E2E 通过，其中 F1 覆盖真实登录、恢复、退出、失败、过期、深链接、角色隔离与移动导航；
- `python -m pytest tests/test_phase1_auth.py -q`：4 个后端认证测试通过。

真实联调：

- `POST /api/auth/login`：通过；
- `GET /api/auth/me`：通过；
- 后端缺口：无；范围偏移：否。


---

## 11. F2 执行记录（2026-07-10）

状态：已完成

已完成：

- 用户列表、服务端分页参数、300ms 关键词搜索与 URL 状态同步；
- Loading、Empty、Error、刷新与小屏横向表格行为；
- 创建用户，字段严格对应 `UserCreate`，提交防重复；
- 用户名/邮箱 409 冲突与 422 字段错误映射；
- 角色修改、至少一个角色校验与精确用户列表缓存失效；
- `sys_admin` 路由/导航开放，`employee` 直接访问进入 Forbidden；
- 未提供删除、禁用、密码重置或角色定义编辑。

验证：

- `npm run typecheck`、`npm run lint`、`npm test`、`npm run build` 全部通过；
- 13 个测试文件、40 个单元/组件测试通过；
- 8 个 Chromium E2E 通过；
- F2 真实 E2E 完成列表、搜索、创建与角色修改；
- `python -m pytest tests/test_phase1_auth.py -q`：4 个后端 Auth/User 测试通过。

真实联调：

- `GET /api/users`：通过；
- `POST /api/users`：通过；
- `PUT /api/users/{user_id}/roles`：通过；
- 后端缺口：无；范围偏移：否。
