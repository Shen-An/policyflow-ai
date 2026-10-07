import { $, browser, expect } from '@wdio/globals'

import { navigateTo, signIn } from './support/app'
import { resetStubState } from './support/stub-client'

// T125 [US4] Every runtime state has a clear reason AND an executable next step:
// loading / empty / error / offline / recovery / permission / conflict. Each is driven
// in the REAL desktop UI — offline via window events, the edge states via reproducible
// workflow scenarios (run.input.scenario) that return the matching backend outcome.

async function launchScenario(scenario: string): Promise<void> {
  await navigateTo('workspace')
  // A workflow may still be active from a previous test (one shared Electron session);
  // return to the selection view so the scenario launchers are visible.
  const back = await $('[data-testid="workflow-back"]')
  if (await back.isExisting()) await back.click()
  const button = await $(`[data-testid="scenario-${scenario}"]`)
  await button.waitForDisplayed({ timeout: 10_000 })
  await button.click()
  await $('[data-testid="workflow-page"]').waitForDisplayed({ timeout: 15_000 })
}

describe('T125 runtime state recovery', () => {
  before(async () => {
    await resetStubState()
    await signIn()
  })

  it('shows an empty state with an actionable next step', async () => {
    await navigateTo('chat')
    const empty = await $('[data-testid="chat-empty"]')
    await empty.waitForDisplayed({ timeout: 10_000 })
    // The empty state is actionable (clickable example question).
    await expect($('[data-testid="chat-example"]')).toBeDisplayed()
  })

  it('surfaces an offline banner then a recovery affordance', async () => {
    await navigateTo('chat')
    await browser.execute(() => window.dispatchEvent(new Event('offline')))
    const offline = await $('[data-testid="state-offline"]')
    await offline.waitForDisplayed({ timeout: 10_000 })
    expect(await offline.getText()).toContain('网络')

    await browser.execute(() => window.dispatchEvent(new Event('online')))
    await $('[data-testid="state-recovery"]').waitForDisplayed({ timeout: 10_000 })
    await expect($('[data-testid="state-recovery"]')).toBeDisplayed()
  })

  it('explains a permission denial with a next step', async () => {
    await launchScenario('permission')
    await $('[data-testid="approval-approve"]').click()
    const permission = await $('[data-testid="state-permission"]')
    await permission.waitForDisplayed({ timeout: 15_000 })
    await expect(permission.$('[data-testid="state-action"]')).toBeDisplayed()
  })

  it('explains a stale/conflict decision with a next step', async () => {
    await launchScenario('conflict')
    await $('[data-testid="approval-approve"]').click()
    const conflict = await $('[data-testid="state-conflict"]')
    await conflict.waitForDisplayed({ timeout: 15_000 })
    await expect(conflict.$('[data-testid="state-action"]')).toBeDisplayed()
  })

  it('explains a failed run with a retry next step', async () => {
    await launchScenario('error')
    const error = await $('[data-testid="state-error"]')
    await error.waitForDisplayed({ timeout: 15_000 })
    const action = await error.$('[data-testid="state-action"]')
    await expect(action).toBeDisplayed()
    expect(await action.getText()).toMatch(/重试|再试/u)
  })
})
