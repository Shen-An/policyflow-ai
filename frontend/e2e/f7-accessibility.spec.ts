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
  await expect(page.getByRole('heading', { name: '登录 PolicyFlow AI' })).toBeVisible()
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
    '/admin/skills',
    '/admin/integrations',
  ]
  for (const route of routes) {
    await page.goto(route)
    await page.waitForLoadState('networkidle')
    await expectNoAccessibilityViolations(page, route)
  }
})
