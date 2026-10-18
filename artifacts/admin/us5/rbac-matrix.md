# US5 管理员治理 — 双租户 RBAC 矩阵

生成时间：2026-10-18T12:17:58.741845+00:00

同一份应用（SQLite 双租户 fixture，见 `tests/support_admin.py`），每个单元格都是实际 HTTP 状态码；括号内是稳定错误码。

| 主体 | `/api/v2/admin/roles` | `/api/v2/admin/grants` | `/api/v2/admin/quota` | `/api/v2/admin/health` | `/api/v2/admin/runs` | `/api/v2/admin/reconciliation` | `/api/v2/admin/capacity` | `/api/v2/admin/runs/30000000-0000-0000-0000-000000000001/audit` | `/api/v2/admin/runs/30000000-0000-0000-0000-000000000002/audit` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alpha_admin | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 403 (AUTH_FORBIDDEN) | 200 (ok) | 404 (RESOURCE_NOT_FOUND) |
| alpha_employee | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) | 403 (AUTH_FORBIDDEN) |
| alpha_platform(cross_tenant) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 404 (RESOURCE_NOT_FOUND) |
| beta_admin | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 200 (ok) | 403 (AUTH_FORBIDDEN) | 404 (RESOURCE_NOT_FOUND) | 200 (ok) |

说明：

- `alpha_employee` 没有 `admin` 动作，所有读取都是 403，且拒绝动作会先写审计再返回。
- `beta_admin` 管理自己的租户；跨租户的 run 一律 404，不暴露“存在但无权”。
- `alpha_platform` 持有独立的 `cross_tenant_admin`，只有它能读 `/capacity`。
- 文件路径里用 `` ` `` 包裹的是请求路径，不是可枚举的资源。
