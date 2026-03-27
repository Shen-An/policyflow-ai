# PolicyFlow AI 前端生产部署与回滚

状态：F7 生产基线  
日期：2026-07-11

## 1. 架构基线

生产环境使用同源反向代理：浏览器只访问一个 HTTPS Origin，`/api/**` 与 `/health` 转发到 FastAPI，其他路径由静态服务器返回前端产物。

- 前端：`frontend/dist/`；
- 后端：FastAPI `:8000`，不直接暴露到公网；
- Base URL：`VITE_API_BASE_URL=`，保持空值；
- 因为浏览器请求同源，生产不依赖宽松 CORS；如必须跨域部署，应由后端单独增加精确 Origin 白名单，不允许 `*` 与凭据组合；
- 参考 Nginx 配置：`frontend/deploy/nginx.conf.example`。

## 2. 构建环境

复制 `frontend/.env.production.example` 为部署系统中的生产环境配置。禁止把 Secret 放进任何 `VITE_*` 变量，Vite 会把这些值写入浏览器产物。

```bash
cd frontend
npm ci
npm run verify:production
```

生产门禁会检查：

- 环境变量白名单；
- `VITE_ENABLE_MSW=false`；
- TypeScript、ESLint 和全部 Vitest；
- 不生成 Source Map；
- 静态资源文件名带内容哈希；
- 产物不包含 MSW、环回地址、E2E 账号或测试密码；
- 使用生产 `dist/` 和真实 FastAPI 运行全部 Playwright；
- 生产依赖不存在 high/critical npm audit 问题。

## 3. 发布步骤

1. 从锁定的提交执行 `npm ci`，不得使用漂移安装；
2. 执行 `npm run verify:production`；
3. 将 `frontend/dist/` 制作为不可变版本制品，记录 Git SHA 与制品 SHA-256；
4. 将制品上传到新版本目录，不覆盖当前目录；
5. 验证 Nginx 配置后切换静态目录软链接或发布版本；
6. 检查 `/health`、`/login`、匿名深链接和管理员登录；
7. 观察 401/403/5xx、前端资源 404、请求超时和 `X-Request-ID`；
8. 验收后保留至少前一个稳定制品。

## 4. 缓存与 Source Map

- `index.html`：`no-store`，确保新版本入口及时生效；
- SPA 路由：`no-cache`；
- `/assets/*`：文件名包含内容哈希，`max-age=31536000, immutable`；
- 生产 Source Map：关闭，不上传到公共静态目录；如未来接入私有错误平台，应通过独立 CI 私密上传，不随站点发布。

## 5. 回滚

1. 停止继续放量；
2. 将静态目录指针切回上一个已验收制品；
3. reload Nginx，不中断现有连接；
4. 验证 `/health`、登录、受保护深链接和一个真实查询；
5. 记录失败版本、request_id、错误摘要和制品 SHA；
6. 前端回滚不回退数据库。若发布同时包含后端迁移，必须按后端迁移计划单独处理。

## 6. 发布后安全检查

- 页面不存在测试账号提示、调试数据或 Mock Worker；
- 浏览器存储中只有当前 sessionStorage 会话，不存在 Secret；
- MCP 配置只显示脱敏摘要；
- 外部链接使用安全 rel；
- CSP、frame、referrer 和 permissions headers 生效；
- 未实现模块不出现在导航或生产路由；
- 定期执行 `npm audit --omit=dev --audit-level=high` 并更新锁文件。
