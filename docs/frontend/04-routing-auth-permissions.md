# 04. 路由、认证与权限计划

状态：Draft  
上游：`01-scope-and-boundaries.md`、`02-technical-architecture.md`  
主要用于：F1 以及所有权限相关任务

## 本文负责

- 路由表和导航；
- 登录会话生命周期；
- 角色与资源权限门禁；
- 401、403 和重定向行为。

## 本文不负责

- API 通用错误模型；
- 页面业务组件；
- 后端鉴权实现。

---

## 1. 路由表

| 路由 | 页面 | 权限 | API 状态要求 |
|---|---|---|---|
| `/login` | 登录 | 匿名 | 已满足 |
| `/` | 默认跳转 | 已登录 | 已满足 |
| `/chat` | 制度问答 | 登录用户 | Chat API |
| `/chat/:conversationId` | 会话 | 所有者/管理员 | Conversation API |
| `/drafts` | 草稿 | 登录用户 | Draft API |
| `/drafts/:draftId` | 草稿详情 | 所有者/管理员 | Draft API |
| `/knowledge-bases` | 知识库 | 登录用户 | KB API |
| `/knowledge-bases/:kbId` | 知识库详情 | resource read | KB API |
| `/knowledge-bases/:kbId/documents` | 文档 | resource write/admin | Document API |
| `/faq-review` | FAQ 审核 | kb/sys admin | FAQ API |
| `/admin/users` | 用户 | sys_admin | 已满足 |
| `/admin/skills` | Skill | sys_admin | Skill API |
| `/admin/integrations` | MCP | sys_admin | MCP API |
| `/admin/audit` | 审计 | sys_admin | Audit API |
| `/evaluation` | 评估 | kb/sys admin | Eval API |
| `/forbidden` | 无权限 | 已登录 | 无 |
| `*` | 404 | 任意 | 无 |

设计态 API 对应路由在生产默认关闭，不发送必然失败的请求。

## 2. 导航

```text
工作台
  制度问答
  我的草稿
知识管理
  知识库
  FAQ 审核
质量与运维
  评估中心
  审计日志
系统管理
  用户管理
  Skill 管理
  MCP 集成
```

菜单按角色与 API Readiness 同时过滤。

## 3. Auth 状态

```ts
type AuthState = {
  accessToken: string | null;
  expiresAt: number | null;
  user: AuthUser | null;
  status: 'booting' | 'authenticated' | 'anonymous';
};
```

生命周期：

```text
应用启动
 -> 读取 sessionStorage
 -> 无 token：anonymous
 -> 有 token：调用 /api/auth/me
    -> 成功：authenticated
    -> 401/过期：清理并 anonymous
```

当前方案：Token 存内存并同步 `sessionStorage`，不使用 `localStorage`。后端支持 HttpOnly Refresh Token 后替换 Auth adapter，页面不变。

## 4. 登录跳转

- 匿名访问受保护页面：保存目标 URL，跳转 `/login`；
- 登录成功：返回目标 URL；
- 无目标时：普通用户优先 `/chat`，Chat 未开放时使用第一个可访问页面；
- 已登录访问 `/login`：跳转默认页；
- 退出：清理 Token、Query Cache 中敏感数据和目标地址。

## 5. 角色门禁

```ts
type RoleCode = 'employee' | 'kb_admin' | 'sys_admin';
function hasAnyRole(actual: RoleCode[], required: RoleCode[]): boolean;
```

三层控制：

1. 导航层：不显示无权入口；
2. 路由层：直接访问跳 `/forbidden`；
3. 操作层：隐藏或禁用具体操作。

前端门禁不替代后端权限校验。

## 6. 资源权限

知识库相关操作使用 API 返回的：

```ts
type ResourcePermission = 'read' | 'write' | 'admin';
```

规则：

- read：查看知识库和文档；
- write：上传、重新索引；
- admin：管理资源配置和权限；
- 不根据 `kb_admin` 角色自动假定对任意知识库有 admin 权限，除非后端契约明确。

## 7. 401/403

- 任意受保护请求 401：只触发一次全局会话失效，清理并跳登录；
- 登录接口 401：显示凭据错误，不触发循环跳转；
- 403 页面请求：展示无权限页面；
- 403 局部写操作：保留页面，显示操作级错误；
- 不把 403 静默映射为空列表。

## 8. F1 允许范围

允许：

```text
frontend/src/app/**
frontend/src/auth/**
frontend/src/features/login/**
frontend/src/components/layout/**
frontend/src/components/feedback/**
相关测试
```

禁止提前创建 Chat、KB、Eval 的业务实现。

## 9. 完成条件

- 登录、恢复、退出、过期行为可测试；
- 深链接登录后可返回；
- sys_admin 与 employee 路由隔离；
- 401 不产生重定向循环；
- 403 有页面级和操作级反馈；
- 未开放模块不出现在生产导航。
