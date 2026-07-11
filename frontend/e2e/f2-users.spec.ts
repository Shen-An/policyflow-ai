import { expect, test } from '@playwright/test'

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('frontend-e2e-only')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/$/u)
  await page.getByRole('link', { name: '用户管理' }).click()
  await expect(page).toHaveURL(/\/admin\/users$/u)
}

test('sys_admin lists, searches, creates, and updates a user through real APIs', async ({ page }) => {
  await loginAsAdmin(page)
  await expect(page.getByRole('button', { name: '创建用户' })).toBeVisible()

  const suffix = String(Date.now())
  const username = `e2e_user_${suffix}`
  await page.getByRole('button', { name: '创建用户' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('用户名').fill(username)
  await dialog.getByLabel('邮箱').fill(`${username}@example.com`)
  await dialog.getByLabel('显示名').fill('E2E 用户')
  await dialog.getByLabel('初始密码').fill('e2e-password')
  await dialog.getByRole('button', { name: '创建用户' }).click()
  await expect(dialog).not.toBeVisible()

  const search = page.getByPlaceholder('搜索用户名、邮箱或显示名')
  await search.fill(username)
  const row = page.getByRole('row', { name: new RegExp(username, 'u') })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: '修改角色' }).click()
  const roleDialog = page.getByRole('dialog')
  await roleDialog.getByLabel('知识库管理员').check()
  await roleDialog.getByRole('button', { name: '保存角色' }).click()
  await expect(roleDialog).not.toBeVisible()
  await expect(row).toContainText('kb_admin')
})


