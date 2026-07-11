import { expect, test } from '@playwright/test'

test('employee asks with evidence, submits feedback, restores history, and completes a draft', async ({ page }) => {
  test.setTimeout(60_000)
  const suffix = String(Date.now())
  const draftTitle = `E2E Travel Request ${suffix}`

  await page.goto('/login')
  await page.getByLabel('用户名').fill('frontend_employee')
  await page.getByLabel('密码').fill('employee-password')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).not.toHaveURL(/\/login$/u)

  await page.getByRole('link', { name: '制度问答' }).click()
  await expect(page).toHaveURL(/\/chat$/u)
  await page.getByLabel('问题').fill('What is the travel approval process?')
  await page.getByRole('button', { name: '发送问题' }).click()

  const answerCard = page.getByRole('article', { name: 'PolicyFlow 回答' })
  await expect(answerCard).toContainText('Travel requests require manager approval')
  await answerCard.getByText('查看引用（1）').click()
  await expect(answerCard).toContainText('Travel Policy')
  await expect(answerCard).toContainText('Travel requests require manager approval.')
  await answerCard.getByLabel('回答评价').selectOption('useful')
  await answerCard.getByLabel('反馈备注').fill('E2E citation verified')
  await answerCard.getByRole('button', { name: '提交反馈' }).click()
  await expect(answerCard.getByRole('status')).toContainText('已记录')

  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+$/u)
  await page.reload()
  const restoredAnswer = page.getByRole('article', { name: 'PolicyFlow 回答' })
  await expect(restoredAnswer).toContainText('Travel requests require manager approval')
  await expect(restoredAnswer.getByLabel('回答评价')).toBeVisible()

  await page.getByRole('link', { name: '我的草稿' }).click()
  await page.getByRole('button', { name: '创建草稿' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('类型').selectOption('email')
  await dialog.getByLabel('标题').fill(draftTitle)
  await dialog.getByLabel('正文').fill('Initial E2E travel request.')
  await dialog.getByLabel('来源问题').fill('Draft a travel request')
  await dialog.getByRole('button', { name: '创建草稿' }).click()

  await expect(page).toHaveURL(/\/drafts\/[0-9a-f-]+$/u)
  await page.getByLabel('正文').fill('Updated E2E travel request.')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page.getByRole('button', { name: '确认草稿' })).toBeEnabled()
  await page.getByRole('button', { name: '确认草稿' }).click()
  await expect(page.getByText('当前状态为 confirmed，正文已只读。')).toBeVisible()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出 Markdown' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe(`${draftTitle}.md`)
})


