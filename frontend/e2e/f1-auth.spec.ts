import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page, username: string, password: string) {
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
}

test('anonymous deep link returns after real admin login, restores, and logs out', async ({ page }) => {
  await page.goto('/admin/users')
  await expect(page).toHaveURL(/\/login$/u)
  expect(await page.evaluate(() => sessionStorage.getItem('policyflow.auth.return-to'))).toBe('/admin/users')
  await login(page, 'admin', 'frontend-e2e-only')
  await expect(page).toHaveURL(/\/admin\/users$/u)
  await expect(page.getByRole('heading', { name: '用户管理', level: 2 })).toBeVisible()
  await expect(page.getByRole('button', { name: '创建用户' })).toBeVisible()

  await page.reload()
  await expect(page).toHaveURL(/\/admin\/users$/u)
  await expect(page.getByRole('button', { name: '创建用户' })).toBeVisible()

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login$/u)
})

test('invalid credentials show a stable error and keep the form', async ({ page }) => {
  await page.goto('/login')
  await login(page, 'admin', 'wrong-password')
  await expect(page.getByRole('alert')).toContainText('用户名或密码不正确')
  await expect(page.getByLabel('用户名')).toHaveValue('admin')
})

test('employee is redirected to forbidden for sys_admin route', async ({ page }) => {
  await page.goto('/admin/users')
  await login(page, 'frontend_employee', 'employee-password')
  await expect(page).toHaveURL(/\/forbidden$/u)
  await expect(page.getByRole('alert').getByRole('heading', { name: '无访问权限' })).toBeVisible()
})

test('expired local session is cleared before protected content is shown', async ({ page }) => {
  await page.goto('/login')
  await login(page, 'admin', 'frontend-e2e-only')
  await expect(page).toHaveURL(/\/$/u)
  await page.evaluate(() => {
    sessionStorage.setItem('policyflow.auth.session', JSON.stringify({ accessToken: 'expired', expiresAt: Date.now() - 1 }))
  })
  await page.reload()
  await expect(page).toHaveURL(/\/login$/u)
  await expect(page.getByRole('heading', { name: '登录 PolicyFlow AI' })).toBeVisible()
})


test('mobile AppShell drawer is keyboard dismissible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/login')
  await login(page, 'admin', 'frontend-e2e-only')
  const trigger = page.getByRole('button', { name: '打开导航' })
  await expect(trigger).toBeVisible()
  await trigger.click()
  await expect(page.getByRole('navigation', { name: '主导航' }).filter({ visible: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '打开导航' })).toHaveAttribute('aria-expanded', 'false')
})


