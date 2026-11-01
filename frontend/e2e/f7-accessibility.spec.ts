import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

async function expectNoAccessibilityViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(results.violations, `${label}: ${JSON.stringify(results.violations, null, 2)}`).toEqual([])
}

test('public and authenticated production routes pass automated accessibility checks', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/login')
  // 登录页品牌标题就是 `PolicyFlow AI`（frontend/src/features/login/login-page.tsx:78），
  // 表单 Card 的标题才是「登录账户」；原来的整串定位器匹配不到任何元素。
  await expect(page.getByRole('heading', { name: 'PolicyFlow AI' })).toBeVisible()
  await expectNoAccessibilityViolations(page, 'login')

  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('frontend-e2e-only')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible()

  const routes = [
    '/',
    '/chat',
    '/drafts',
    '/knowledge-bases',
    '/faq-review',
    '/evaluation',
    '/admin/audit',
    '/admin/users',
    '/admin/governance',
    '/admin/runs',
    '/admin/reconciliation',
    '/admin/skills',
    '/admin/integrations',
  ]
  for (const route of routes) {
    // createHashRouter（frontend/src/app/router.tsx:36）：路由在 `#` 之后，
    // 直接 goto 路径只会落在 index 路由，扫描到的就不是目标页面。
    await page.goto(`/#${route}`)
    await page.waitForLoadState('networkidle')
    await expectNoAccessibilityViolations(page, route)
  }
})
