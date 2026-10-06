import { shell } from 'electron'
import type { Session, WebContents } from 'electron'

import {
  allowedExternalHosts,
  buildContentSecurityPolicy,
  isExternalLinkAllowed,
  isSameLocalRenderer,
} from './hardening'

// Re-export the pure policy so `security.ts` is the single public surface for CSP,
// the external-link allowlist and the navigation rule, while the logic itself stays
// in the electron-free `hardening` module for direct unit testing.
export {
  allowedExternalHosts,
  buildContentSecurityPolicy,
  isExternalLinkAllowed,
  isSameLocalRenderer,
}

export type NavigationGuardOptions = {
  rendererUrl: string
  hosts?: ReadonlySet<string>
  openExternal?: (url: string) => void
}

/**
 * Deny-by-default session hardening: strict CSP response header on every load,
 * and refusal of every permission request/check (camera, geolocation, etc.) and
 * device access (USB/serial/bluetooth).
 */
export function applySessionSecurity(targetSession: Session): void {
  const csp = buildContentSecurityPolicy()
  targetSession.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = { ...details.responseHeaders }
    // Drop any upstream CSP so ours is authoritative, then set the strict policy.
    for (const key of Object.keys(responseHeaders)) {
      if (key.toLowerCase() === 'content-security-policy') delete responseHeaders[key]
    }
    responseHeaders['Content-Security-Policy'] = [csp]
    callback({ responseHeaders })
  })
  targetSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
  targetSession.setPermissionCheckHandler(() => false)
  targetSession.setDevicePermissionHandler(() => false)
}

/**
 * Lock a WebContents to its controlled renderer: block full-page navigation and
 * redirects away from it, deny all new windows, and route only allowlisted https
 * links to the OS browser. Nothing else may open.
 */
export function installNavigationGuards(
  webContents: WebContents,
  options: NavigationGuardOptions,
): void {
  const hosts = options.hosts ?? allowedExternalHosts()
  const openExternal = options.openExternal ?? ((url: string) => void shell.openExternal(url))

  const blockOffRendererNavigation = (event: { preventDefault: () => void }, url: string) => {
    if (!isSameLocalRenderer(url, options.rendererUrl)) event.preventDefault()
  }
  webContents.on('will-navigate', blockOffRendererNavigation)
  webContents.on('will-redirect', blockOffRendererNavigation)

  webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalLinkAllowed(url, hosts)) openExternal(url)
    return { action: 'deny' }
  })

  // Webview tags are disabled in webPreferences; refuse attach as defense in depth.
  webContents.on('will-attach-webview', (event: { preventDefault: () => void }) =>
    event.preventDefault(),
  )
}
