import { browser, expect } from '@wdio/globals'

// T110 — the renderer must run as a locked-down sandboxed page: context isolation on,
// Node integration off, OS sandbox on, and no raw IPC / arbitrary file / token API on
// the page. The only bridge is the minimal typed `window.policyflow` surface.

const EXPECTED_BRIDGE_KEYS = ['approvals', 'auth', 'materials', 'runs', 'system', 'workspace']

async function readRendererEnvironment() {
  return browser.execute(() => {
    const w = window as unknown as Record<string, unknown>
    const bridge = w.policyflow as Record<string, unknown> | undefined
    const keysOf = (value: unknown): string[] =>
      value && typeof value === 'object' ? Object.keys(value as object).sort() : []
    return {
      hasBridge: typeof bridge === 'object' && bridge !== null,
      bridgeKeys: keysOf(bridge),
      authKeys: keysOf(bridge?.auth),
      runsKeys: keysOf(bridge?.runs),
      systemKeys: keysOf(bridge?.system),
      // Node / CommonJS escapes that must NOT exist on a sandboxed renderer.
      hasRequire: typeof w.require !== 'undefined',
      hasProcess: typeof w.process !== 'undefined',
      hasModule: typeof w.module !== 'undefined',
      hasGlobal: typeof w.global !== 'undefined',
      hasBuffer: typeof w.Buffer !== 'undefined',
      hasElectron: typeof w.electron !== 'undefined',
      // Raw IPC must never be reachable, directly or via the bridge.
      hasIpcRendererGlobal: typeof w.ipcRenderer !== 'undefined',
      bridgeExposesRawIpc: bridge
        ? ['ipcRenderer', 'ipc', 'invoke', 'send', 'sendSync', 'on', 'postMessage'].some(
            (key) => key in bridge,
          )
        : false,
      // No arbitrary filesystem or token capability may be exposed.
      bridgeExposesFs: bridge
        ? ['fs', 'readFile', 'writeFile', 'path', 'openPath', 'readFileSync'].some(
            (key) => key in bridge,
          )
        : false,
      authExposesToken: bridge?.auth
        ? ['token', 'getToken', 'accessToken', 'getAccessToken', 'refreshToken'].some(
            (key) => key in (bridge.auth as object),
          )
        : false,
    }
  })
}

describe('T110 renderer capability sandbox', () => {
  before(async () => {
    await browser.waitUntil(
      async () => (await browser.execute(() => typeof (window as unknown as { policyflow?: unknown }).policyflow)) === 'object',
      { timeout: 15_000, timeoutMsg: 'window.policyflow was never exposed' },
    )
  })

  it('enforces contextIsolation, sandbox and no node integration in webPreferences', async () => {
    const prefs = await browser.electron.execute((electron) => {
      const win = electron.BrowserWindow.getAllWindows()[0]
      const webContents = win?.webContents as unknown as
        | { getLastWebPreferences: () => Record<string, unknown> | null }
        | undefined
      return webContents?.getLastWebPreferences() ?? null
    })
    expect(prefs).not.toBe(null)
    expect(prefs?.contextIsolation).toBe(true)
    expect(prefs?.sandbox).toBe(true)
    expect(prefs?.nodeIntegration).toBe(false)
    expect(prefs?.webviewTag).toBe(false)
    expect(prefs?.webSecurity).not.toBe(false)
  })

  it('exposes only the minimal typed bridge, with no node/CommonJS escapes', async () => {
    const env = await readRendererEnvironment()
    expect(env.hasBridge).toBe(true)
    expect(env.bridgeKeys).toEqual(EXPECTED_BRIDGE_KEYS)
    expect(env.hasRequire).toBe(false)
    expect(env.hasProcess).toBe(false)
    expect(env.hasModule).toBe(false)
    expect(env.hasGlobal).toBe(false)
    expect(env.hasBuffer).toBe(false)
    expect(env.hasElectron).toBe(false)
  })

  it('never exposes raw IPC, arbitrary file access or a token getter', async () => {
    const env = await readRendererEnvironment()
    expect(env.hasIpcRendererGlobal).toBe(false)
    expect(env.bridgeExposesRawIpc).toBe(false)
    expect(env.bridgeExposesFs).toBe(false)
    expect(env.authExposesToken).toBe(false)
    expect(env.authKeys).toEqual(['currentUser', 'login', 'logout'])
    expect(env.systemKeys).toEqual(['info', 'openExternal'])
  })

  it('reports no node access through the system.info capability', async () => {
    const info = await browser.execute(async () => {
      const bridge = (window as unknown as { policyflow: { system: { info: () => Promise<unknown> } } }).policyflow
      const result = (await bridge.system.info()) as { ok?: boolean; value?: Record<string, unknown> }
      return result
    })
    expect(info.ok).toBe(true)
    expect(info.value?.hasNodeAccess).toBe(false)
  })
})
