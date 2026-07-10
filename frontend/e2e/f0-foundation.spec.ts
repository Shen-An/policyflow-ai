import { expect, test } from '@playwright/test'

test('F0 renders and Vite proxies the real FastAPI health endpoint', async ({ page, request }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'PolicyFlow AI' })).toBeVisible()
  await expect(page.getByRole('link', { name: '检查后端健康状态' })).toHaveAttribute('href', '/health')
  const response = await request.get('/health')
  expect(response.ok()).toBe(true)
  expect(await response.json()).toEqual({ status: 'ok' })
})

test('unknown routes expose an accessible not-found state', async ({ page }) => {
  await page.goto('/missing')
  await expect(page.getByRole('heading', { name: '页面不存在' })).toBeVisible()
  await expect(page.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/')
})
