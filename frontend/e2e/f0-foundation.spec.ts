import { expect, test } from '@playwright/test'

test('F0 build remains available and Vite proxies the real FastAPI health endpoint', async ({ page, request }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '登录 PolicyFlow AI' })).toBeVisible()
  const response = await request.get('/health')
  expect(response.ok()).toBe(true)
  expect(await response.json()).toEqual({ status: 'ok' })
})

test('unknown routes expose an accessible not-found state', async ({ page }) => {
  await page.goto('/missing')
  await expect(page.getByRole('heading', { name: '页面不存在' })).toBeVisible()
  await expect(page.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/')
})
