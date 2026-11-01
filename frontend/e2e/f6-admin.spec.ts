import { expect, test } from '@playwright/test'

test('admin runs Skills, inspects redacted Tool logs, and manages mock MCP integrations', async ({ page }) => {
  test.setTimeout(60_000)
  await page.goto('/#/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('frontend-e2e-only')
  await page.getByRole('button', { name: '登录' }).click()

  await page.getByRole('link', { name: 'Skill 管理' }).click()
  await expect(page.getByRole('heading', { name: 'Skill 管理', level: 1 })).toBeVisible()
  const summaryRow = page.getByRole('row').filter({ hasText: 'summary' })
  await expect(summaryRow).toContainText('已实现')
  await summaryRow.getByRole('button', { name: '手动运行' }).click()
  const runDialog = page.getByRole('dialog', { name: '运行 summary' })
  await runDialog.getByLabel('运行参数').fill(JSON.stringify({ text: 'Annual leave requires approval.' }))
  await runDialog.getByRole('button', { name: '确认运行' }).click()
  await expect(runDialog.getByText(/Audit ID：/u)).toBeVisible()
  await expect(runDialog.getByText(/Request ID：/u)).toBeVisible()
  // The close affordance is `<span aria-label="关闭运行对话框">` nested inside the
  // antd modal close button (skills-page.tsx:263), so the button itself carries no
  // accessible name; target the real control by its stable class instead.
  await runDialog.locator('.ant-modal-close').click()
  await expect(runDialog).not.toBeVisible()

  await summaryRow.getByRole('button', { name: '禁用' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '禁用' }).click()
  await expect(summaryRow).toContainText('已禁用')
  await summaryRow.getByRole('button', { name: '启用' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '启用' }).click()
  await expect(summaryRow).toContainText('已启用')

  const toolRow = page.getByRole('row').filter({ hasText: 'mcp.call' }).first()
  // The table renders the localized status chip 成功 (lib/labels.ts
  // toolLogStatusLabel), not the raw `success` column value.
  await expect(toolRow).toContainText('成功')
  await toolRow.getByRole('button', { name: '查看' }).click()
  const toolDialog = page.getByRole('dialog', { name: 'Tool 日志详情' })
  await toolDialog.getByText('脱敏输入参数').click()
  await expect(toolDialog).toContainText('[REDACTED]')
  await expect(toolDialog).toContainText('visible')
  // ToolLogDialog likewise relies on the antd default close control, which is
  // icon-only (skills-page.tsx:509-512): dismiss it by its stable class.
  await toolDialog.locator('.ant-modal-close').click()
  await expect(toolDialog).not.toBeVisible()

  await page.getByRole('link', { name: 'MCP 集成' }).click()
  await expect(page.getByRole('heading', { name: 'MCP 集成', level: 1 })).toBeVisible()
  const seedCard = page.locator('.ant-card').filter({ hasText: 'frontend_f6_mock' })
  await expect(seedCard).toContainText('MOCK')
  await seedCard.getByRole('button', { name: '健康检查' }).click()
  // The post-health-check Alert renders with role="alert" (integrations-page.tsx
  // :184-198) and reports the status string returned by the mock adapter.
  await expect(page.getByRole('alert').filter({ hasText: '健康检查完成' })).toContainText('healthy')
  await expect(seedCard).toContainText('mcp.email.create_draft')

  await seedCard.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.getByRole('dialog', { name: '编辑 MCP 集成' })
  await editDialog.getByLabel('名称').fill('frontend_f6_mock_updated')
  await editDialog.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('frontend_f6_mock_updated')).toBeVisible()

  await page.getByRole('button', { name: '创建 MCP' }).click()
  const createDialog = page.getByRole('dialog', { name: '创建 MCP 集成' })
  await createDialog.getByLabel('名称').fill(`f6_ui_mock_${Date.now()}`)
  await createDialog.getByLabel('启用集成').check()
  await createDialog.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText(/f6_ui_mock_/u)).toBeVisible()
})

test('employee cannot open F6 administration routes', async ({ page }) => {
  await page.goto('/#/login')
  await page.getByLabel('用户名').fill('frontend_employee')
  await page.getByLabel('密码').fill('employee-password')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible()

  await page.goto('/#/admin/skills')
  await expect(page.getByRole('alert').getByRole('heading', { name: '无访问权限' })).toBeVisible()
  await page.goto('/#/admin/integrations')
  await expect(page.getByRole('alert').getByRole('heading', { name: '无访问权限' })).toBeVisible()
})
