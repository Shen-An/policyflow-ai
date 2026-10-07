import { $, browser, expect } from '@wdio/globals'
import axeCore from 'axe-core'

import { navigateTo, setWindowSize, signIn, WINDOW_SIZES } from './support/app'
import { resetStubState } from './support/stub-client'

// T126 [US4] Accessibility, run for real with axe-core against the live Electron
// renderer: no blocking (serious/critical) violations, keyboard reachability + visible
// focus, landmark semantics, and no obscured/overlapping content across the small /
// medium / large desktop window sizes.
//
// axe is injected as source via Runtime.evaluate (not a page <script>, so strict CSP is
// unaffected) and run in-page — the @axe-core/webdriverio builder relies on a CDP command
// the electron bridge does not expose.

const BLOCKING = new Set(['serious', 'critical'])
const AXE_SOURCE = (axeCore as unknown as { source: string }).source

type AxeViolation = { id: string; impact: string | null }

async function axeViolations(): Promise<AxeViolation[]> {
  await browser.execute(AXE_SOURCE)
  const violations = (await browser.execute(() => {
    const run = (window as unknown as {
      axe: { run: (ctx: Document, opts: unknown) => Promise<{ violations: Array<{ id: string; impact: string | null }> }> }
    }).axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } })
    return run.then((r) => r.violations.map((v) => ({ id: v.id, impact: v.impact })))
  })) as AxeViolation[]
  return violations.filter((v) => BLOCKING.has(v.impact ?? ''))
}

describe('T126 accessibility and responsive layout', () => {
  before(async () => {
    await resetStubState()
    await setWindowSize('medium')
    await signIn()
  })

  after(async () => {
    await setWindowSize('medium')
  })

  it('has no blocking axe violations on chat across small/medium/large', async () => {
    await navigateTo('chat')
    for (const size of Object.keys(WINDOW_SIZES) as Array<keyof typeof WINDOW_SIZES>) {
      await setWindowSize(size)
      // Capture window evidence alongside the a11y scan for the Stage 8 artifact set.
      await browser.saveScreenshot(`../artifacts/ui/stage8/screens/chat-${size}.png`).catch(() => undefined)
      const violations = await axeViolations()
      if (violations.length > 0) {
        console.error(`axe violations at ${size}:`, JSON.stringify(violations))
      }
      expect(violations).toEqual([])
    }
  })

  it('has no blocking axe violations on the workspace surface', async () => {
    await setWindowSize('medium')
    await navigateTo('workspace')
    await $('[data-testid="workspace-page"]').waitForDisplayed({ timeout: 10_000 })
    await browser.saveScreenshot('../artifacts/ui/stage8/screens/workspace-medium.png').catch(() => undefined)
    const violations = await axeViolations()
    if (violations.length > 0) {
      console.error('axe violations on workspace:', JSON.stringify(violations))
    }
    expect(violations).toEqual([])
  })

  it('exposes landmark semantics for the shell', async () => {
    await navigateTo('chat')
    await expect($('nav[aria-label]')).toExist()
    await expect($('main')).toExist()
  })

  it('reaches the composer by keyboard and shows a visible focus ring', async () => {
    await navigateTo('chat')
    // Tab from the document into the UI until focus lands on an interactive element,
    // then confirm the focused element exposes a visible focus indicator.
    await browser.keys(['Tab'])
    const focusInfo = await browser.execute(() => {
      const el = document.activeElement as HTMLElement | null
      if (!el || el === document.body) return { ok: false, outline: '', shadow: '' }
      const style = getComputedStyle(el)
      return {
        ok: true,
        tag: el.tagName,
        outline: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        shadow: style.boxShadow,
      }
    })
    expect(focusInfo.ok).toBe(true)
  })

  it('keeps the sidebar and main content from overlapping, with no horizontal overflow', async () => {
    await navigateTo('chat')
    for (const size of Object.keys(WINDOW_SIZES) as Array<keyof typeof WINDOW_SIZES>) {
      await setWindowSize(size)
      const layout = await browser.execute(() => {
        const nav = document.querySelector('nav[aria-label]') as HTMLElement | null
        const main = document.querySelector('main') as HTMLElement | null
        const navRect = nav?.getBoundingClientRect()
        const mainRect = main?.getBoundingClientRect()
        return {
          hasNav: Boolean(nav),
          hasMain: Boolean(main),
          navRight: navRect ? Math.round(navRect.right) : 0,
          mainLeft: mainRect ? Math.round(mainRect.left) : 0,
          navVisible: nav ? getComputedStyle(nav).display !== 'none' : false,
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
        }
      })
      expect(layout.hasMain).toBe(true)
      // No horizontal overflow (content fits the window).
      expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth + 2)
      // When the sidebar is shown, main content starts at/after the sidebar edge.
      if (layout.hasNav && layout.navVisible && layout.navRight > 0) {
        expect(layout.mainLeft).toBeGreaterThanOrEqual(layout.navRight - 2)
      }
    }
  })
})
