import path from 'node:path'

import { app, safeStorage, session, Menu } from 'electron'
import type { WebContents } from 'electron'

import { ApiProxy } from './api-proxy'
import { createCredentialVault } from './credentials'
import { registerIpcHandlers } from './ipc'
import {
  APP_RENDERER_URL,
  registerAppProtocolSchemes,
  serveRendererOverAppProtocol,
} from './protocol'
import { applySessionSecurity, installNavigationGuards } from './security'
import type { SenderOriginOptions } from './origin'
import { createMainWindow } from './window'

// Force sandboxing for every renderer process, app-wide, before anything loads, and
// register the privileged app:// scheme the controlled renderer is served over.
app.enableSandbox()
registerAppProtocolSchemes()

const BACKEND_BASE_URL = process.env.POLICYFLOW_API_BASE_URL ?? 'http://127.0.0.1:8000'
const DEV_RENDERER_URL = process.env.ELECTRON_RENDERER_URL ?? null

/** The directory holding the built renderer (dist/), relative to the main bundle. */
function rendererRoot(): string {
  // __dirname (CJS bundle) is dist-electron/main; the renderer is dist/.
  return path.join(__dirname, '../../dist')
}

/** The URL the renderer loads: dev server when provided, else app://local. */
function rendererUrl(): string {
  return DEV_RENDERER_URL ?? APP_RENDERER_URL
}

function preloadPath(): string {
  return path.join(__dirname, '../preload/index.cjs')
}

function bootstrap(): void {
  const renderer = rendererUrl()
  const origin: SenderOriginOptions = { rendererUrl: renderer, devServerUrl: DEV_RENDERER_URL }

  // No application menu — reduces accelerator/role surface.
  Menu.setApplicationMenu(null)

  // Serve the controlled renderer over app:// (unless a dev server is in use).
  if (!DEV_RENDERER_URL) serveRendererOverAppProtocol(rendererRoot())

  // Session-wide hardening (CSP header, permission + device deny-by-default).
  applySessionSecurity(session.defaultSession)

  // Guard every WebContents that is ever created, not just the main window.
  app.on('web-contents-created', (_event, contents: WebContents) => {
    installNavigationGuards(contents, { rendererUrl: renderer })
    contents.on('will-attach-webview', (event) => event.preventDefault())
  })

  const vault = createCredentialVault(
    {
      isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
      encryptString: (plain) => safeStorage.encryptString(plain),
      decryptString: (buffer) => safeStorage.decryptString(buffer),
    },
    path.join(app.getPath('userData'), 'policyflow-credentials.bin'),
  )

  const proxy = new ApiProxy({ backendBaseUrl: BACKEND_BASE_URL, vault })

  const disposeIpc = registerIpcHandlers({ proxy, origin, appVersion: app.getVersion() })
  app.on('will-quit', disposeIpc)

  createMainWindow({ preloadPath: preloadPath(), rendererUrl: renderer })
}

// Single-instance: a second launch focuses the existing window rather than opening
// another privileged renderer.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  void app.whenReady().then(bootstrap)

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
}
