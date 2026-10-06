import { browser, expect } from '@wdio/globals'

// T112 — navigation + content hardening: strict CSP (behaviourally and as a header),
// denial of full-page navigation to remote origins, denial of new windows, and the
// external-link allowlist routing only approved https hosts to the OS browser.

describe('T112 navigation and content security', () => {
  before(async () => {
    await browser.waitUntil(
      async () =>
        (await browser.execute(
          () => typeof (window as unknown as { policyflow?: unknown }).policyflow,
        )) === 'object',
      { timeout: 15_000, timeoutMsg: 'window.policyflow was never exposed' },
    )
  })

  after(async () => {
    await browser.electron.restoreAllMocks()
  })

  it('enforces a strict CSP that blocks inline script injection', async () => {
    const result = await browser.execute(async () => {
      let violatedDirective: string | null = null
      const onViolation = (event: SecurityPolicyViolationEvent) => {
        violatedDirective = event.violatedDirective
      }
      document.addEventListener('securitypolicyviolation', onViolation)
      try {
        // A dynamically inserted inline script must be refused by script-src 'self'.
        const script = document.createElement('script')
        script.textContent = 'window.__cspInlineExecuted = true'
        document.head.appendChild(script)
      } catch {
        /* ignore */
      }
      await new Promise((resolve) => setTimeout(resolve, 100))
      document.removeEventListener('securitypolicyviolation', onViolation)
      return {
        inlineExecuted: Boolean((window as unknown as { __cspInlineExecuted?: boolean }).__cspInlineExecuted),
        violatedDirective: violatedDirective ?? '',
      }
    })
    expect(result.inlineExecuted).toBe(false)
    expect(result.violatedDirective.includes('script-src')).toBe(true)
  })

  it('sends the strict CSP header on the controlled renderer document', async () => {
    const csp = await browser.execute(async () => {
      const response = await fetch('index.html', { cache: 'no-store' })
      return response.headers.get('content-security-policy')
    })
    expect(typeof csp).toBe('string')
    expect(csp ?? '').toContain("default-src 'self'")
    expect(csp ?? '').toContain("script-src 'self'")
    expect(csp ?? '').toContain("object-src 'none'")
    // Scripts must stay strict — no inline/eval escape hatch.
    expect((csp ?? '').includes("'unsafe-eval'")).toBe(false)
    expect(/script-src[^;]*'unsafe-inline'/u.test(csp ?? '')).toBe(false)
  })

  it('blocks full-page navigation away from the controlled renderer', async () => {
    const before = await browser.getUrl()
    expect(before.startsWith('app://local')).toBe(true)
    await browser.execute(() => {
      try {
        window.location.href = 'https://example.com/'
      } catch {
        /* navigation prevented */
      }
    })
    await browser.pause(800)
    const after = await browser.getUrl()
    expect(after.startsWith('app://local')).toBe(true)
  })

  it('denies opening new windows', async () => {
    const openedNull = await browser.execute(() => {
      const child = window.open('https://example.com/', '_blank')
      return child === null
    })
    expect(openedNull).toBe(true)
    const windowCount = await browser.electron.execute(
      (electron) => electron.BrowserWindow.getAllWindows().length,
    )
    expect(windowCount).toBe(1)
  })

  it('routes only allowlisted https links to the OS browser', async () => {
    // Mock shell.openExternal so an allowlisted link does not actually launch a
    // browser. The handler returns opened:true only after invoking shell.openExternal,
    // so the opened flags are the authoritative record of the allowlist decision.
    await browser.electron.mock('shell', 'openExternal')

    const allowed = (await browser.execute(() =>
      (window as unknown as {
        policyflow: { system: { openExternal: (r: unknown) => Promise<unknown> } }
      }).policyflow.system.openExternal({ url: 'https://help.policyflow.example/guide' }),
    )) as { ok: boolean; value?: { opened: boolean } }
    const denied = (await browser.execute(() =>
      (window as unknown as {
        policyflow: { system: { openExternal: (r: unknown) => Promise<unknown> } }
      }).policyflow.system.openExternal({ url: 'https://evil.example/phish' }),
    )) as { ok: boolean; value?: { opened: boolean } }

    expect(allowed.ok).toBe(true)
    expect(allowed.value?.opened).toBe(true)
    expect(denied.ok).toBe(true)
    expect(denied.value?.opened).toBe(false)
  })
})
