import { $, browser, expect } from '@wdio/globals'

import { navigateTo, signIn } from './support/app'
import { readStubState, resetStubState } from './support/stub-client'

// T123 [US4] Chat workflow in the real desktop UI: a clickable empty state, a quiet
// compact staged thinking timeline (details collapsed by default) fed by ordered run
// events, evidence, a copyable Markdown answer, a grounded refusal when evidence is
// insufficient, user-message copy/edit, scroll-to-latest, and recovery after an SSE
// disconnect. Driven end to end against real Electron + the deterministic stub.

async function askQuestion(text: string): Promise<void> {
  const input = await $('[data-testid="chat-input"]')
  await input.waitForDisplayed({ timeout: 10_000 })
  await input.click()
  await browser.keys(text.split(''))
  await $('[data-testid="chat-send"]').click()
}

describe('T123 chat workflow', () => {
  before(async () => {
    await resetStubState()
    await signIn()
  })

  beforeEach(async () => {
    // Remount the chat page fresh for each test (one shared Electron session keeps the
    // component mounted otherwise, so turns/draft would leak across tests).
    await navigateTo('workspace')
    await navigateTo('chat')
  })

  it('offers a clickable example from the empty state', async () => {
    await $('[data-testid="chat-empty"]').waitForDisplayed({ timeout: 10_000 })
    const example = await $('[data-testid="chat-example"]')
    await expect(example).toBeDisplayed()
    await example.click()
    // Clicking an example seeds the composer with that question.
    const value = await $('[data-testid="chat-input"]').getValue()
    expect((value ?? '').length).toBeGreaterThan(0)
  })

  it('runs a supported question: quiet collapsed timeline, evidence, copyable answer', async () => {
    await askQuestion('市内交通费可以报销吗？')

    // Compact staged timeline appears and keeps its detail collapsed by default.
    const thinking = await $('[data-testid="thinking-process"]')
    await thinking.waitForDisplayed({ timeout: 15_000 })
    const details = await $('[data-testid="thinking-details"]')
    expect(await details.isDisplayed().catch(() => false)).toBe(false)
    await $('[data-testid="thinking-toggle"]').click()
    await expect($('[data-testid="thinking-details"]')).toBeDisplayed()

    // Evidence and a Markdown answer with a copy affordance.
    await $('[data-testid="chat-evidence"]').waitForDisplayed({ timeout: 15_000 })
    const answer = await $('[data-testid="assistant-message"]')
    await answer.waitForDisplayed({ timeout: 15_000 })
    expect(await answer.getText()).toContain('结论')
    await expect($('[data-testid="answer-copy"]')).toExist()
  })

  it('lets the user copy and edit their own message', async () => {
    await askQuestion('差旅住宿标准是多少？')
    const userMsg = await $('[data-testid="user-message"]')
    await userMsg.waitForDisplayed({ timeout: 15_000 })
    await userMsg.moveTo()
    await expect($('[data-testid="user-copy"]')).toBeDisplayed()
    await $('[data-testid="user-edit"]').click()
    // Editing re-seeds the composer with the original text for correction + resend.
    const value = await $('[data-testid="chat-input"]').getValue()
    expect(value).toContain('差旅住宿标准')
  })

  it('shows a grounded refusal when evidence is insufficient', async () => {
    await askQuestion('关于 no-evidence 的事项如何处理？')
    const refusal = await $('[data-testid="chat-refusal"]')
    await refusal.waitForDisplayed({ timeout: 15_000 })
    expect(await refusal.getText()).toContain('依据')
  })

  it('recovers the answer after an SSE disconnect and reconnect', async () => {
    await askQuestion('reconnect 这个问题的制度是什么？')

    // The renderer surfaces an explicit reconnecting state when the stream drops…
    await $('[data-testid="chat-reconnecting"]').waitForDisplayed({ timeout: 15_000 })
    // …then recovers and renders the terminal answer after re-subscribing.
    await $('[data-testid="assistant-message"]').waitForDisplayed({ timeout: 20_000 })

    // The backend saw more than one event-stream open for the reconnecting run.
    const state = await readStubState()
    const reconnectRun = Object.values(state.s8EventStreamOpensByRun).some((n) => n >= 2)
    expect(reconnectRun).toBe(true)
  })
})
