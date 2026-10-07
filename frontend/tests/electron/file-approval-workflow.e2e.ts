import { $, $$, browser, expect } from '@wdio/globals'

import { navigateTo, signIn } from './support/app'
import { readStubState, resetStubState } from './support/stub-client'

// T124 [US4] Reimbursement file-approval workflow in the real desktop UI:
// select authorized material → draft → tree / preview / version / diff (all generated
// files stay draft before approval) → approve/reject → result. The change set and the
// approval target (action, destination, exact files/hashes, side effects, expiry) arrive
// as ordered run-event payloads; the decision goes back through approvals.decide.

async function startReimbursementWorkflow(): Promise<void> {
  await navigateTo('workspace')
  // One shared Electron session keeps a prior workflow active; return to the selection
  // view first so the material picker is shown.
  const back = await $('[data-testid="workflow-back"]')
  if (await back.isExisting()) await back.click()
  await $('[data-testid="workspace-page"]').waitForDisplayed({ timeout: 10_000 })
  // Pick an authorized material, then kick off the file workflow.
  const material = await $('[data-testid="material-option"]')
  await material.waitForDisplayed({ timeout: 10_000 })
  await material.click()
  await $('[data-testid="workflow-start"]').click()
  await $('[data-testid="workflow-page"]').waitForDisplayed({ timeout: 15_000 })
}

describe('T124 file-approval workflow', () => {
  before(async () => {
    await resetStubState()
    await signIn()
  })

  it('builds a draft change set with tree, preview, version and diff', async () => {
    await startReimbursementWorkflow()

    // File tree with the generated reimbursement files.
    const tree = await $('[data-testid="file-tree"]')
    await tree.waitForDisplayed({ timeout: 15_000 })
    const nodes = await $$('[data-testid="file-node"]')
    expect(await nodes.length).toBeGreaterThanOrEqual(2)

    // Generated files are drafts until approved.
    await expect($('[data-testid="draft-badge"]')).toBeDisplayed()

    // Selecting a node reveals preview, version and diff for that file.
    await nodes[0].click()
    await expect($('[data-testid="file-preview"]')).toBeDisplayed()
    await expect($('[data-testid="file-version"]')).toBeDisplayed()
    const diff = await $('[data-testid="file-diff"]')
    await expect(diff).toBeDisplayed()
    expect((await diff.getText()).length).toBeGreaterThan(0)
  })

  it('surfaces the approval target, exact files/hashes, side effects and expiry', async () => {
    await startReimbursementWorkflow()
    await $('[data-testid="approval-target"]').waitForDisplayed({ timeout: 15_000 })
    expect(await $('[data-testid="approval-target"]').getText()).toContain('财务系统')
    await expect($('[data-testid="approval-files"]')).toBeDisplayed()
    await expect($('[data-testid="approval-side-effects"]')).toBeDisplayed()
    await expect($('[data-testid="approval-expiry"]')).toBeDisplayed()
  })

  it('approves and shows the submission result', async () => {
    await startReimbursementWorkflow()
    const approve = await $('[data-testid="approval-approve"]')
    await approve.waitForDisplayed({ timeout: 15_000 })
    await approve.click()

    const result = await $('[data-testid="submission-result"]')
    await result.waitForDisplayed({ timeout: 15_000 })
    expect(await result.getText()).toContain('RCPT-')

    // The decision reached the backend through approvals.decide with an s8 approval id.
    await browser.waitUntil(async () => (await readStubState()).s8ApprovalDecisions.length >= 1, {
      timeout: 10_000,
      timeoutMsg: 'approval decision never reached the backend',
    })
    const decisions = (await readStubState()).s8ApprovalDecisions
    expect(decisions.some((d) => d.decision === 'approve' && d.approvalId.startsWith('s8-'))).toBe(true)
  })

  it('rejects and reports the rejected outcome', async () => {
    await startReimbursementWorkflow()
    const reject = await $('[data-testid="approval-reject"]')
    await reject.waitForDisplayed({ timeout: 15_000 })
    await reject.click()

    const result = await $('[data-testid="workflow-rejected"]')
    await result.waitForDisplayed({ timeout: 15_000 })
    await expect(result).toBeDisplayed()
    await browser.waitUntil(
      async () => (await readStubState()).s8ApprovalDecisions.some((d) => d.decision === 'reject'),
      { timeout: 10_000, timeoutMsg: 'reject decision never reached the backend' },
    )
  })
})
