import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, username: string, password: string) {
  await page.goto('/#/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).not.toHaveURL(/\/login$/u)
}

test('admin creates a real knowledge base and indexed document while employee remains read-only', async ({ page }) => {
  test.setTimeout(60_000)
  const suffix = String(Date.now())
  const knowledgeBaseName = `E2E 人力制度库 ${suffix}`
  const knowledgeBaseCode = `e2e-hr-${suffix}`
  const documentTitle = `E2E 员工手册 ${suffix}`

  await login(page, 'admin', 'frontend-e2e-only')
  await page.goto('/#/knowledge-bases')
  await expect(page).toHaveURL(/\/knowledge-bases$/u)

  await page.locator('.page-toolbar').getByRole('button', { name: '创建知识库' }).click()
  const createDialog = page.getByRole('dialog')
  await createDialog.getByLabel('名称').fill(knowledgeBaseName)
  await createDialog.getByLabel('编码').fill(knowledgeBaseCode)
  await createDialog.getByLabel('部门').selectOption({ label: 'HR（hr）' })
  await createDialog.getByLabel('默认检索模式').selectOption('hybrid')
  await createDialog.getByLabel('描述').fill('真实 E2E 创建的人力资源制度库')
  await createDialog.getByRole('button', { name: '创建知识库' }).click()
  await expect(createDialog).not.toBeVisible()

  const card = page.locator('.kb-card').filter({ hasText: knowledgeBaseName })
  await expect(card).toBeVisible()
  await expect(card).toContainText('管理')
  await card.getByRole('link', { name: '查看详情' }).click()
  await expect(page.getByRole('heading', { name: knowledgeBaseName, level: 2 })).toBeVisible()

  await page.getByRole('link', { name: '管理文档' }).click()
  await page.getByRole('button', { name: '上传文档' }).click()
  const uploadDialog = page.getByRole('dialog')
  await uploadDialog.getByLabel('文件').setInputFiles({
    name: `employee-handbook-${suffix}.txt`,
    mimeType: 'text/plain',
    buffer: Buffer.from('PolicyFlow E2E employee handbook content.', 'utf8'),
  })
  await uploadDialog.getByLabel('标题（可选）').fill(documentTitle)
  await uploadDialog.getByRole('button', { name: '上传文档' }).click()
  await expect(uploadDialog).not.toBeVisible()

  const documentRow = page.getByRole('row', { name: new RegExp(documentTitle, 'u') })
  await expect(documentRow).toBeVisible()
  await expect(documentRow).toContainText('已索引', { timeout: 15_000 })

  await page.getByRole('button', { name: '退出登录' }).click()
  await login(page, 'frontend_employee', 'employee-password')
  await page.goto('/#/knowledge-bases')

  const employeeCard = page.locator('.kb-card').filter({ hasText: knowledgeBaseName })
  await expect(employeeCard).toBeVisible()
  await expect(employeeCard).toContainText('只读')
  await employeeCard.getByRole('link', { name: '查看详情' }).click()
  await expect(page.getByText('只读', { exact: true }).first()).toBeVisible()
  await page.getByRole('link', { name: '管理文档' }).click()
  await expect(page.getByText('当前为只读权限，不能上传或重新索引。')).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(documentTitle, 'u') })).toBeVisible()
  await expect(page.getByRole('button', { name: '上传文档' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重新索引' })).toHaveCount(0)
})


