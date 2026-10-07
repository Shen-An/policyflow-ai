import { $, browser, expect } from '@wdio/globals'

// Shared UI-level helpers for the Stage 8 Electron E2E suites. Unlike the Phase 7 specs
// (which drive the `window.policyflow` bridge directly), Stage 8 exercises the REAL
// rendered desktop UI: keyboard-driven sign-in, navigation, and the chat / approval
// surfaces. The security boundary and the backend (deterministic stub) are unchanged.

export const SIGN_IN = { username: 'employee', password: 'secret-pw' }

/** Common desktop window sizes exercised by the accessibility / layout spec. */
export const WINDOW_SIZES = {
  small: { width: 1024, height: 680 },
  medium: { width: 1280, height: 800 },
  large: { width: 1680, height: 1050 },
} as const

export async function waitForBridge(): Promise<void> {
  await browser.waitUntil(
    async () =>
      (await browser.execute(
        () => typeof (window as unknown as { policyflow?: unknown }).policyflow,
      )) === 'object',
    { timeout: 15_000, timeoutMsg: 'window.policyflow was never exposed' },
  )
}

/** Hash-router navigation that works under the app://local custom scheme. */
export async function gotoHash(path: string): Promise<void> {
  await browser.execute((p) => {
    window.location.hash = p
  }, path)
  await browser.pause(150)
}

async function isAuthenticated(): Promise<boolean> {
  return (await $('[data-testid="app-shell"]').isExisting())
}

/**
 * Sign in through the real login form using the keyboard, then wait for the unified
 * desktop shell. Idempotent: returns early if already authenticated.
 */
export async function signIn(): Promise<void> {
  await waitForBridge()
  if (await isAuthenticated()) return

  // Anonymous boot lands on /login (ProtectedRoute redirect). Make sure we are there.
  const username = await $('input[autocomplete="username"]')
  await username.waitForDisplayed({ timeout: 15_000 })
  await username.click()
  await browser.keys(SIGN_IN.username.split(''))

  const password = await $('input[autocomplete="current-password"]')
  await password.click()
  await browser.keys(SIGN_IN.password.split(''))
  await browser.keys(['Enter'])

  await $('[data-testid="app-shell"]').waitForDisplayed({ timeout: 20_000 })
}

export async function signOutIfPossible(): Promise<void> {
  const logout = await $('[data-testid="shell-logout"]')
  if (await logout.isExisting()) await logout.click()
}

/** Click a primary-nav destination by its stable testid, e.g. 'chat', 'workspace'. */
export async function navigateTo(section: string): Promise<void> {
  const link = await $(`[data-testid="nav-${section}"]`)
  await link.waitForDisplayed({ timeout: 10_000 })
  await link.click()
  await browser.pause(150)
}

export async function setWindowSize(size: keyof typeof WINDOW_SIZES): Promise<void> {
  const { width, height } = WINDOW_SIZES[size]
  // `browser.setWindowSize` relies on a CDP command the electron bridge does not expose;
  // resize the real BrowserWindow content area through the main process instead.
  await browser.electron.execute(
    (electron, w, h) => {
      const win = electron.BrowserWindow.getAllWindows()[0]
      win?.setContentSize(Math.round(w as number), Math.round(h as number))
    },
    width,
    height,
  )
  await browser.pause(300)
}

export async function expectVisible(testid: string, timeout = 10_000): Promise<void> {
  await $(`[data-testid="${testid}"]`).waitForDisplayed({ timeout })
  await expect($(`[data-testid="${testid}"]`)).toBeDisplayed()
}
