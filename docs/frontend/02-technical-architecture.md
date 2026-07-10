# 02. 前端技术架构与目录

状态：Draft  
上游：`01-scope-and-boundaries.md`  
主要用于：Frontend Phase F0

## 本文负责

- 技术栈、应用分层和目录；
- 部署与开发代理；
- 模块依赖方向；
- 允许和禁止的抽象方式。

## 本文不负责

- 具体页面交互；
- 角色权限规则；
- API 字段；
- 业务功能排期。

---

## 1. 锁定技术栈

| 分类 | 选择 |
|---|---|
| 构建 | Vite |
| UI | React + TypeScript |
| 路由 | React Router |
| 服务端状态 | TanStack Query |
| 表单 | React Hook Form + Zod |
| 样式 | Tailwind CSS + CSS Variables |
| 基础组件 | 本地 shadcn/ui 风格组件 + Radix primitives |
| 图标 | Lucide |
| 单元/组件测试 | Vitest + React Testing Library |
| Mock | MSW |
| E2E | Playwright |

不默认使用 Redux、Next.js、微前端、SSR 或大型图表库。

## 2. 分层

```text
app/router/providers
        ↓
feature pages and components
        ↓
feature API hooks
        ↓
api client / normalizer
        ↓
FastAPI
```

依赖规则：

- `api` 不依赖 `features`；
- `components/ui` 不依赖业务 API；
- `features` 不相互深层导入内部文件；
- 页面不得直接调用 `fetch`；
- 生成 API 代码与手写 adapter 分离；
- Query 数据不复制到全局 Store。

## 3. 推荐目录

```text
frontend/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  .env.example

  src/
    app/
      App.tsx
      router.tsx
      providers.tsx
      query-client.ts
      route-guards.tsx

    api/
      client.ts
      errors.ts
      response-normalizer.ts
      generated/
      auth.ts
      users.ts
      knowledge-bases.ts
      chat.ts
      drafts.ts
      faq.ts
      skills.ts
      tools.ts
      mcp.ts
      audit.ts
      eval.ts

    auth/
      auth-store.ts
      auth-session.ts
      permissions.ts
      use-auth.ts

    components/
      ui/
      layout/
      feedback/
      data-display/
      forms/

    features/
      login/
      chat/
      knowledge-bases/
      documents/
      drafts/
      faq-review/
      users/
      skills/
      integrations/
      audit/
      evaluation/

    hooks/
    lib/
    styles/
    test/
    types/

  e2e/
```

## 4. 目录约束

- Feature 内可包含 `api.ts`、`queries.ts`、`components/`、`pages/`、`schemas.ts`；
- 两个以上 feature 真实复用后才能上移公共组件；
- 不建立无业务含义的 `utils/common/shared2`；
- `api/generated/` 若启用代码生成，禁止手改；
- 环境变量统一通过 `lib/env.ts` 校验；
- 设计令牌只在 `styles/tokens.css` 定义。

## 5. 开发和部署

开发：

```text
http://localhost:5173 -> Vite
/api, /health         -> proxy 到 http://localhost:8000
```

生产优先同域反向代理。若分域：

- FastAPI 配置精确 Origin；
- 不使用通配 CORS + credentials；
- 前端 Base URL 来自环境变量；
- 不把任何 Secret 注入 `VITE_*`。

## 6. 代码分割

- 登录页独立首屏；
- 管理模块路由级懒加载；
- Eval、MCP 等低频页面单独 Chunk；
- 不为小组件过度拆包；
- 生产构建必须关闭 MSW。

## 7. F0 允许修改的范围

```text
frontend/**
```

F0 默认禁止修改：

```text
backend/**
docs/01-*.md ... docs/05-*.md
数据库文件
```

若 CORS 或接口契约阻塞，登记后端缺口，不在 F0 顺手修后端。

## 8. 完成条件

- 工程可安装、启动、构建；
- Router、Query、测试和 MSW 基础设施可用；
- 目录依赖方向明确；
- 开发代理可访问 `/health`；
- 无业务页面提前实现；
- lockfile 已提交且生产 Mock 被禁止。


---

## 9. F0 执行记录（2026-07-10）

状态：已完成

已完成：

- 已创建 React + TypeScript + Vite 工程并生成 `package-lock.json`；
- Router、TanStack Query、React Hook Form、Zod、Tailwind CSS 与 Radix 基础组件依赖已锁定；
- 已实现统一 API Client、响应归一化、AppError、API Readiness 与环境变量校验；
- 已配置 Vitest、React Testing Library、MSW 和 Playwright；
- Vite 代理已通过真实 FastAPI `GET /health` E2E；
- 生产构建检查确认不包含 MSW worker 或 Mock 启动代码；
- 未创建 F1～F7 业务页面，未修改 `backend/**` 或 `docs/01`～`docs/05`。

验证：

- `npm run typecheck`：通过；
- `npm run lint`：通过；
- `npm test`：5 个测试文件、19 个测试通过；
- `npm run build`：通过，生产 Mock 检查通过；
- `npm run e2e`：2 个 Chromium E2E 通过；
- `python -m pytest tests/test_phase0.py -q`：5 个后端健康检查相关测试通过。

完成确认：

- 用户已于 2026-07-10 明确授权 Git Commit；`package-lock.json`、F0 实现与阶段状态文档纳入同一 F0 提交，完成条件已满足。
