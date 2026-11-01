import { expect, test } from '@playwright/test'

test('admin reviews FAQ, inspects audit, runs answer evaluation, and debugs retrieval', async ({ page }) => {
  // FAQ review + audit log + preparing the eval test set + one eval Run.
  test.setTimeout(180_000)
  await page.goto('/#/login')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('frontend-e2e-only')
  await page.getByRole('button', { name: '登录' }).click()

  await page.getByRole('link', { name: 'FAQ 审核' }).click()
  const firstFAQ = page.getByRole('article').filter({ hasText: '常见问题 1' })
  const secondFAQ = page.getByRole('article').filter({ hasText: '常见问题 2' })
  await expect(firstFAQ).toContainText('F5 Leave Policy')
  await firstFAQ.getByRole('button', { name: '审核通过' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '审核通过' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'FAQ 文档索引状态' })).toBeVisible()
  await secondFAQ.getByRole('button', { name: '驳回' }).click()
  const rejectDialog = page.getByRole('dialog')
  await rejectDialog.getByLabel('驳回原因').fill('E2E duplicate topic')
  await rejectDialog.getByRole('button', { name: '确认驳回' }).click()
  await expect(rejectDialog).not.toBeVisible()

  await page.getByRole('link', { name: '审计日志' }).click()
  await expect(page.getByRole('heading', { name: '审计日志', level: 1 })).toBeVisible()
  // The audit table row action is a "详情" link button (audit-page.tsx:190-198).
  await page.getByRole('button', { name: '详情' }).first().click()
  const auditDialog = page.getByRole('dialog')
  await expect(auditDialog.getByText('脱敏详情')).toBeVisible()
  await expect(auditDialog.getByText('Request ID')).toBeVisible()
  // The audit detail Modal (audit-page.tsx:306-313) uses the antd default close
  // control, which is an icon-only button without an accessible name.
  await page.locator('.ant-modal-close').click()
  await expect(page.getByRole('dialog')).not.toBeVisible()

  await page.getByRole('link', { name: '评估中心' }).click()

  // The page is a guided flow now: the hand-managed use-case form and the
  // single-shot retrieval debugger live in collapsed "高级…" panels
  // (evaluation-page.tsx:333-346), so their fields only exist once expanded.
  await page.getByRole('button', { name: /高级：手工管理评估用例/u }).click()
  // `getByLabel` matches substrings, so the bare '问题' also matched the
  // 评测问题数 (sample size) number input; the use-case field label is exactly
  // 问题 (evaluation-page.tsx:639-645).
  await page.getByLabel('问题', { exact: true }).fill('How is annual leave approved?')
  await page.getByLabel('知识库分类代码').fill('hr')
  await page.getByLabel('期望关键词（逗号分隔）').fill('manager approval')
  await page.getByLabel('期望来源标题（逗号分隔）').fill('Travel Policy')
  await page.getByRole('button', { name: '创建用例' }).click()
  // Created cases only render inside the nested "展开查看已有用例" collapse, as
  // `enabled|disabled · {category} · {question}` (evaluation-page.tsx:757-773),
  // so the panel has to be expanded before the row exists in the DOM.
  await page.getByRole('button', { name: /展开查看已有用例/u }).click()
  await expect(page.getByText(/enabled · hr · How is annual leave approved/u)).toBeVisible()

  // A Run may only use the isolated eval KBs — the 评测知识库 select lists
  // nothing else (evaluation-page.tsx:1120-1132) — and the e2e seed does not
  // populate them, so prepare the bundled enterprise policy test set first.
  // That action also selects the resulting KB into the Run form (:502-511).
  await page.getByRole('button', { name: '准备企业政策测试集' }).click()
  await expect(page.getByText(/企业政策测试集已准备/u)).toBeVisible({ timeout: 30_000 })

  const runName = `F5 E2E ${Date.now()}`
  await page.getByRole('button', { name: /高级选项（策略对比/u }).click()
  await page.getByLabel('Run 名称').fill(runName)
  await page.getByRole('button', { name: '随机 50' }).click()
  await expect(page.getByText(/将用 \d+ 条检索用例启动/u)).toBeVisible()
  await page.getByRole('button', { name: '启动评估' }).click()
  // 评估历史 renders one row per Run with a localized status chip
  // (evaluation-page.tsx:893-897 + statusLabel at :64-72).
  const runRow = page.getByRole('row').filter({ hasText: runName })
  await expect(runRow).toBeVisible({ timeout: 30_000 })
  // The 评估历史 list is not polled (queries.ts:46-53 polls only a single run
  // detail), so the terminal status only shows up after a reload; the Run itself
  // is executed asynchronously by the backend.
  await expect
    .poll(
      async () => {
        await page.reload()
        return runRow.textContent()
      },
      { timeout: 120_000, intervals: [2_000] },
    )
    .toContain('成功')
  // formatRate (evaluation-page.tsx:79-83) renders Hit@1/Hit@5/Hit@10/MRR as
  // percentages, and '—' while the run has no metrics yet.
  await expect(runRow).toContainText('%')
  await expect(page.getByRole('columnheader', { name: 'Hit@1', exact: true })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'MRR' })).toBeVisible()

  // Single-shot retrieval debug against the business KB holding the corpus.
  await page.getByRole('button', { name: /高级：单次检索调试/u }).click()
  await page.getByLabel('调试查询').fill('annual leave')
  // The 调试知识库 field has no <label>; evaluation-page.tsx:1896-1907 renders an
  // antd <Select placeholder="请选择知识库">, whose placeholder is a styled span
  // (not the input's placeholder attribute) and whose combobox has no accessible
  // name, so the select is targeted by its rendered placeholder text.
  await page.locator('.ant-select').filter({ hasText: '请选择知识库' }).click()
  // antd renders the clickable options as `.ant-select-item-option` (title = label);
  // the role="option" nodes sit in a 0×0 screen-reader-only listbox.
  const debugKbDropdown = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')
  await debugKbDropdown.locator('.ant-select-item-option[title="人力资源制度库"]').click()
  await page.getByRole('button', { name: '运行调试' }).click()
  await expect(page.getByRole('cell', { name: 'Travel Policy' })).toBeVisible()
})
