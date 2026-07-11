# 05. API、状态与错误合同

状态：Draft  
上游：`docs/03-api-design.md`、当前 FastAPI OpenAPI 和 Schema  
用于：所有接口开发与联调

## 本文负责

- API Client；
- 成功/错误响应归一化；
- TanStack Query 规范；
- API Readiness 和 Mock 边界；
- 长任务轮询。

## 本文不负责

- 具体页面布局；
- 权限角色表；
- 后端 Schema 的实现。

---

## 1. 客户端职责

统一客户端负责：

- Base URL；
- Bearer Token；
- JSON 和 multipart；
- AbortSignal 和超时；
- 401 会话失效；
- 响应归一化；
- request_id 读取；
- 开发日志脱敏。

页面禁止直接 `fetch` 或自行拼 Authorization Header。

## 2. 当前契约差异

设计文档建议成功 Envelope：

```json
{"success": true, "data": {}, "message": "ok", "request_id": "req_xxx"}
```

当前 Auth/User 接口直接返回模型。当前错误形态为：

```json
{"error": {"code": "...", "message": "...", "details": null}}
```

集中兼容：

```ts
type ApiEnvelope<T> = {
  success: true;
  data: T;
  message?: string;
  request_id?: string;
};

function normalizeSuccess<T>(payload: T | ApiEnvelope<T>): T;
function normalizeError(status: number, payload: unknown): AppError;
```

禁止在页面中写 `payload.data ?? payload`。

## 3. 错误模型

```ts
type AppError = {
  kind: 'auth' | 'permission' | 'validation' | 'not-found' |
        'conflict' | 'network' | 'timeout' | 'server' | 'unknown';
  code: string;
  message: string;
  details?: unknown;
  status?: number;
  requestId?: string;
  retryable: boolean;
};
```

| 状态 | 行为 |
|---|---|
| 400 | 业务错误，不自动重试 |
| 401 | 会话失效；登录接口除外 |
| 403 | 页面或操作级无权限 |
| 404 | 资源不存在状态 |
| 409 | 表单冲突，如用户名/邮箱重复 |
| 422 | 尽量映射字段错误 |
| 429 | 稍后重试，读取 Retry-After |
| 5xx | 保留 request_id 和重试入口 |
| 网络错误 | 保留缓存并显示离线/重试 |

## 4. Query Key

```ts
['auth', 'me']
['users', { page, pageSize, keyword }]
['knowledge-bases', filters]
['knowledge-base', kbId]
['documents', kbId, filters]
['conversation', conversationId]
['drafts', filters]
['draft', draftId]
['faq-drafts', filters]
['audit-logs', filters]
['eval-run', runId]
```

Mutation 成功后只失效相关 Key，不清空全部缓存。

## 5. 状态归属

| 状态 | 位置 |
|---|---|
| 服务端数据 | TanStack Query |
| Token/User | Auth Store |
| 搜索/筛选/分页/Tab | URL Query |
| 对话框/展开/局部表单 | 组件状态 |
| 登录前目标地址 | Router state 或 sessionStorage |

禁止把 Query 数据复制到全局 Store。

## 6. 请求重试

- GET：只对网络错误和部分 5xx 有限重试；
- POST/PUT：默认不自动重试；
- 写操作必须防重复；
- 页面卸载取消无效请求；
- 网络恢复后只刷新可见且过期的数据。

## 7. 轮询

通用策略：

- 初始 2 秒；
- 30 秒后退避到 5 秒；
- 页面不可见暂停；
- success/failed 停止；
- 超过前台等待上限只提示后台处理中，不擅自标记失败。

适用：索引、Eval Run、MCP 健康检查等明确长任务。

## 8. API Readiness

```ts
type ApiReadiness =
  | 'implemented'
  | 'contract-only'
  | 'mock-only'
  | 'blocked'
  | 'production-off';
```

规则：

- Auth/User 当前为 `implemented`；
- 其余默认 `contract-only`；
- MSW 只能用于开发和组件测试；
- 生产构建强制关闭 Mock；
- 未就绪功能不发送真实请求。

## 9. OpenAPI 使用

真实联调前：

1. 导出当前 OpenAPI；
2. 对照路径、方法、字段、枚举和错误；
3. 若生成类型，输出到 `api/generated/`；
4. 手写 adapter 转为前端领域模型；
5. 契约变化通过类型错误和测试暴露。

## 10. 完成条件

- 页面无直接 fetch；
- raw/envelope 响应只有一处兼容；
- 401、403、409、422、5xx 行为固定；
- Query Key 集中且失效精确；
- Mock 与生产严格隔离；
- 长任务不会无限轮询。
