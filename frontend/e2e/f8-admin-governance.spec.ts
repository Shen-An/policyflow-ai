import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Response } from '@playwright/test'

// Phase 9 新增的三个 admin 页（租户治理 / 运行与审计 / 一致性修复）此前只有单测，
// 没有 E2E 与 axe 覆盖（见 artifacts/acceptance/SC-001-SC-016.md 的 SC-012）。
// 这里沿用 f6/f7 的写法：真实 e2e 后端（e2e/global-setup.ts 启动并 seed）+ axe wcag2a/wcag2aa。
//
// 注意：本文件刻意把「页面能渲染 / 控件可达 / 无 axe 违规」与「v2 admin 接口返回 200」
// 拆成两类用例。接口契约用例曾经是红的：seed 出来的 sys_admin 角色 actions 为空，
// `/api/v2/admin/*` 对 bootstrap admin 一律 403（详见
// artifacts/acceptance/sc012-admin-e2e-2026-10-21.md）。2026-11-01 已修（种子角色带上
// actions，见 backend/app/db/init_db.py 的 ROLE_SEEDS），该用例现在钉住「六个 admin 读
// 全部 200、capacity 仍是 403」这条契约。

async function expectNoAccessibilityViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(results.violations, `${label}: ${JSON.stringify(results.violations, null, 2)}`).toEqual([])
}

async function loginAs(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible()
}

/** 等一条 `/api/v2/admin/<path>` 的 GET 响应，用来证明页面真的发出了请求（而不是停在加载态）。 */
function waitForAdminGet(page: Page, path: string): Promise<Response> {
  return page.waitForResponse((response) => {
    if (response.request().method() !== 'GET') return false
    return new URL(response.url()).pathname === `/api/v2/admin/${path}`
  })
}

test('tenant governance page renders from the app shell, keeps its controls reachable and passes accessibility checks', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await loginAs(page, 'admin', 'frontend-e2e-only')

  const health = waitForAdminGet(page, 'health')
  const quota = waitForAdminGet(page, 'quota')
  await page.getByTestId('nav-governance').click()
  // 只要求后端给出确定答复（不是 5xx）；403 的分支已经写进下面的接口契约用例。
  expect((await health).status(), 'GET /api/v2/admin/health').toBeLessThan(500)
  expect((await quota).status(), 'GET /api/v2/admin/quota').toBeLessThan(500)

  await expect(page.getByTestId('admin-page')).toBeVisible()
  await expect(page.getByRole('heading', { name: '租户治理', level: 3 })).toBeVisible()
  // 页面自身的说明文字与 Tab 结构不依赖接口数据，属于稳定断言。
  await expect(page.getByText(/队列健康与授权都在当前租户内生效/)).toBeVisible()
  await expect(page.getByRole('tab', { name: '概览' })).toHaveAttribute('aria-selected', 'true')
  // 「队列与索引 / 生效配额策略」两张卡只在 health、quota 查询成功后渲染。
  // 2026-11-01 起种子 sys_admin 带上 admin 动作，这两个接口返回 200（见文件头注释），
  // 但卡片文案属于易变的展示层，仍不在这里钉死，契约交给下面的接口用例。

  // 第二个 Tab 用键盘进入：证明焦点与键盘激活都通，而不是只能鼠标点。
  const accessTab = page.getByRole('tab', { name: '角色与授权' })
  const roles = waitForAdminGet(page, 'roles')
  const grants = waitForAdminGet(page, 'grants')
  await accessTab.focus()
  await page.keyboard.press('Enter')
  expect((await roles).status(), 'GET /api/v2/admin/roles').toBeLessThan(500)
  expect((await grants).status(), 'GET /api/v2/admin/grants').toBeLessThan(500)
  await expect(accessTab).toHaveAttribute('aria-selected', 'true')

  const memberFilter = page.getByLabel('按成员 ID 过滤')
  await expect(memberFilter).toBeVisible()
  await expect(page.getByRole('button', { name: '过滤' })).toBeEnabled()
  await expect(page.getByRole('button', { name: '授予角色' })).toBeEnabled()

  // 键盘可达：聚焦输入框后能直接输入并触发过滤请求。
  const filteredGrants = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname === '/api/v2/admin/grants' && url.searchParams.get('member_id') === 'frontend_employee'
  })
  await memberFilter.focus()
  await page.keyboard.type('frontend_employee')
  await page.getByRole('button', { name: '过滤' }).click()
  expect((await filteredGrants).status(), 'GET /api/v2/admin/grants?member_id=…').toBeLessThan(500)

  // 授予角色的对话框：字段可见、能被取消关闭。
  await page.getByRole('button', { name: '授予角色' }).click()
  const grantDialog = page.getByRole('dialog')
  await expect(grantDialog).toBeVisible()
  await expect(grantDialog.getByPlaceholder('租户内成员的用户 ID')).toBeVisible()
  // 角色是 antd Select，不是 <input placeholder>：控件本身是 role="combobox"，
  // 它的占位文案由 antd 内部渲染（不同版本节点形态不同），因此这里只钉住
  // 「combobox 存在且键盘可达」，文案断言交给角色下拉真正展开时的选项断言。
  const roleSelect = grantDialog.getByRole('combobox')
  await expect(roleSelect).toBeVisible()
  await roleSelect.focus()
  await expect(roleSelect).toBeFocused()
  await grantDialog.getByRole('button', { name: '取消' }).click()
  await expect(grantDialog).toBeHidden()

  await expectNoAccessibilityViolations(page, '/admin/governance')
})

test('run audit page renders from the app shell, keeps its filters keyboard reachable and passes accessibility checks', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await loginAs(page, 'admin', 'frontend-e2e-only')

  const runs = waitForAdminGet(page, 'runs')
  await page.getByTestId('nav-runs').click()
  expect((await runs).status(), 'GET /api/v2/admin/runs').toBeLessThan(500)

  await expect(page.getByTestId('admin-audit-run-page')).toBeVisible()
  await expect(page.getByRole('heading', { name: '运行与审计', level: 3 })).toBeVisible()
  await expect(page.getByText('运行列表')).toBeVisible()
  await expect(page.getByText('正在加载运行…')).toBeHidden()

  // 无障碍在「初次渲染、未与下拉交互」的状态下检查：一旦 rc-select 展开过，
  // 它会在 DOM 里留下一个 height:0/width:0 的隐藏 listbox，axe 会报
  // aria-input-field-name —— 那是 antd 上游组件自己的节点，不是本页的标记，
  // 记录在 artifacts/acceptance/sc012-admin-e2e-2026-11-01.md，不用 disableRules 掩盖。
  await expectNoAccessibilityViolations(page, '/admin/runs')

  // 状态下拉：焦点可达、能展开、能选中，并触发带 status 参数的重新查询。
  const statusFilter = page.getByLabel('按运行状态过滤')
  await expect(statusFilter).toBeEnabled()
  await statusFilter.focus()
  await expect(statusFilter).toBeFocused()
  // antd Select 的展开交给点击（rc-select 对合成的方向键事件不总是响应），
  // 断言的是真正可见的选项，而不是始终留在 DOM 里的隐藏 listbox。
  await statusFilter.click()
  const failedOption = page.locator(
    '.ant-select-dropdown:visible .ant-select-item-option[title="failed"]',
  )
  await expect(failedOption).toBeVisible()

  const filteredRuns = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname === '/api/v2/admin/runs' && url.searchParams.get('status') === 'failed'
  })
  await failedOption.click()
  expect((await filteredRuns).status(), 'GET /api/v2/admin/runs?status=failed').toBeLessThan(500)
  await expect(page.getByText('正在加载运行…')).toBeHidden()
})

test('reconciliation page renders from the app shell, keeps its filters keyboard reachable and passes accessibility checks', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await loginAs(page, 'admin', 'frontend-e2e-only')

  const issues = waitForAdminGet(page, 'reconciliation')
  const capacity = waitForAdminGet(page, 'capacity')
  await page.getByTestId('nav-reconciliation').click()
  expect((await issues).status(), 'GET /api/v2/admin/reconciliation').toBeLessThan(500)
  // 跨租户容量对租户管理员是 403，这也是页面的一种确定渲染分支；这里只要求后端给出确定答复。
  expect((await capacity).status(), 'GET /api/v2/admin/capacity').toBeLessThan(500)

  await expect(page.getByTestId('admin-reconciliation-page')).toBeVisible()
  await expect(page.getByRole('heading', { name: '一致性修复', level: 3 })).toBeVisible()
  // 精确匹配：空状态文案「没有跨存储问题」也包含这个子串。
  await expect(page.getByText('跨存储问题', { exact: true })).toBeVisible()
  // 容量区有两条确定分支：有 cross_tenant_admin 时是「平台容量（跨租户）」卡片，
  // 租户管理员则是 403 提示（reconciliation-page.tsx:43-53）。两条都必须渲染出其中一条。
  await expect(
    page.getByText('平台容量（跨租户）').or(page.getByText('容量证据仅平台管理员可见')),
  ).toBeVisible()

  // 同 runs 用例：axe 在未与下拉交互的初始渲染态检查，交互后 rc-select 留下的
  // height:0 隐藏 listbox 缺 aria-label 属 antd 上游问题，写在验收文档里。
  await expectNoAccessibilityViolations(page, '/admin/reconciliation')

  const stateFilter = page.getByLabel('按问题状态过滤')
  await expect(stateFilter).toBeEnabled()
  await stateFilter.focus()
  await expect(stateFilter).toBeFocused()
  await stateFilter.click()
  const openOption = page.locator(
    '.ant-select-dropdown:visible .ant-select-item-option[title="open"]',
  )
  await expect(openOption).toBeVisible()

  const filteredIssues = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname === '/api/v2/admin/reconciliation' && url.searchParams.get('state') === 'open'
  })
  await openOption.click()
  expect((await filteredIssues).status(), 'GET /api/v2/admin/reconciliation?state=open').toBeLessThan(500)
  await expect(page.getByText('正在加载问题…')).toBeHidden()
})

test('v2 admin endpoints accept the bootstrap sys_admin session', async ({ page }) => {
  test.setTimeout(120_000)
  await loginAs(page, 'admin', 'frontend-e2e-only')

  // 当前真实结果：租户内读接口全部 403（Not authorized to perform this action）。
  // 根因是 backend/app/db/init_db.py:626 建角色时没有写入 roles.actions，
  // 而 admin_service 的租户内读接口都要求 Action.ADMIN（见 artifacts/acceptance/sc012-admin-e2e-2026-10-21.md）。
  // 这条用例钉住「接口契约」，backend 修好后它会自然转绿；页面渲染用例不依赖它。
  const health = waitForAdminGet(page, 'health')
  const quota = waitForAdminGet(page, 'quota')
  await page.getByTestId('nav-governance').click()
  expect((await health).status(), 'GET /api/v2/admin/health').toBe(200)
  expect((await quota).status(), 'GET /api/v2/admin/quota').toBe(200)

  const roles = waitForAdminGet(page, 'roles')
  const grants = waitForAdminGet(page, 'grants')
  await page.getByRole('tab', { name: '角色与授权' }).click()
  expect((await roles).status(), 'GET /api/v2/admin/roles').toBe(200)
  expect((await grants).status(), 'GET /api/v2/admin/grants').toBe(200)

  const runs = waitForAdminGet(page, 'runs')
  await page.getByTestId('nav-runs').click()
  expect((await runs).status(), 'GET /api/v2/admin/runs').toBe(200)

  const issues = waitForAdminGet(page, 'reconciliation')
  const capacity = waitForAdminGet(page, 'capacity')
  await page.getByTestId('nav-reconciliation').click()
  expect((await issues).status(), 'GET /api/v2/admin/reconciliation').toBe(200)
  // 跨租户容量是另一条授权线（Action.CROSS_TENANT_ADMIN，见 backend/app/services/admin_service.py:1166-1180）：
  // 租户管理员被拒 403 是设计如此，这里断言的是「被明确拒绝」而不是 5xx。
  expect((await capacity).status(), 'GET /api/v2/admin/capacity').toBe(403)
})

test('employee cannot open the new administration routes', async ({ page }) => {
  test.setTimeout(120_000)
  await loginAs(page, 'frontend_employee', 'employee-password')

  // RoleGuard（frontend/src/auth/route-guards.tsx:25-28）缺角色时 Navigate 到 /forbidden，
  // 该页由 PermissionDeniedNotice（role="alert" + h1 无访问权限）渲染。
  // 应用用的是 createHashRouter（frontend/src/app/router.tsx:36），路由在 `#` 之后，
  // 所以直接 goto 路径只会落在 index 路由上；这里必须走 hash URL。
  for (const route of ['governance', 'runs', 'reconciliation']) {
    await page.goto(`/#/admin/${route}`)
    await expect(page, `/admin/${route} should redirect to /forbidden`).toHaveURL(/#\/forbidden$/)
    await expect(page.getByRole('alert').getByRole('heading', { name: '无访问权限' })).toBeVisible()
  }
})
