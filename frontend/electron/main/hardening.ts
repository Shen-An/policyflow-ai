// Pure hardening policy for the desktop shell. This module imports NOTHING from
// `electron`, so it can be unit-tested directly (see hardening.test.ts) and reused
// by both the window factory and the session/navigation wiring. There is exactly
// one definition here of the renderer's privileges and the content/navigation rules.

/**
 * The locked-down `webPreferences` for every renderer window.
 *
 * contextIsolation + sandbox + no Node integration means the renderer runs as an
 * ordinary sandboxed web page: no `require`, no `process`, no filesystem, and no way
 * to reach `ipcRenderer` except through the typed bridge installed by the preload.
 */
export const HARDENED_WEB_PREFERENCES = Object.freeze({
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  nodeIntegrationInWorker: false,
  nodeIntegrationInSubFrames: false,
  webviewTag: false,
  webSecurity: true,
  allowRunningInsecureContent: false,
  experimentalFeatures: false,
  spellcheck: false,
})

/**
 * Strict Content-Security-Policy for the desktop renderer.
 *
 * - `script-src 'self'` only: no inline scripts, no `eval`, no remote scripts.
 * - `connect-src 'self'`: the renderer makes NO direct network calls — every
 *   authenticated request is proxied through main — so this blocks exfiltration
 *   and any accidental direct backend/token traffic from a compromised page.
 * - `style-src` allows `'unsafe-inline'` only because the component library emits
 *   inline styles; inline styles cannot execute code. Scripts stay strict.
 */
export function buildContentSecurityPolicy(): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "media-src 'self'",
    "object-src 'none'",
    "frame-src 'none'",
    "child-src 'none'",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ')
}

const DEFAULT_EXTERNAL_HOSTS = ['help.policyflow.example', 'docs.policyflow.example']

/** Allowlisted external hosts: built-in defaults plus `POLICYFLOW_EXTERNAL_HOSTS`. */
export function allowedExternalHosts(
  env: NodeJS.ProcessEnv = process.env,
): ReadonlySet<string> {
  const extra = (env.POLICYFLOW_EXTERNAL_HOSTS ?? '')
    .split(',')
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean)
  return new Set([...DEFAULT_EXTERNAL_HOSTS, ...extra])
}

/** An external link may be opened in the OS browser only if https + allowlisted. */
export function isExternalLinkAllowed(
  url: string,
  hosts: ReadonlySet<string> = allowedExternalHosts(),
): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  return parsed.protocol === 'https:' && hosts.has(parsed.hostname.toLowerCase())
}

/**
 * In-app navigation is confined to the controlled local renderer. SPA client
 * routing uses the History API (no `will-navigate`), so a real `will-navigate`
 * to anything but the exact renderer document is an escape attempt — deny it.
 */
export function isSameLocalRenderer(targetUrl: string, rendererUrl: string): boolean {
  let target: URL
  let renderer: URL
  try {
    target = new URL(targetUrl)
    renderer = new URL(rendererUrl)
  } catch {
    return false
  }
  if (target.protocol !== renderer.protocol) return false
  if (renderer.protocol === 'file:') return target.pathname === renderer.pathname
  // Compare protocol + host rather than `origin`: a custom scheme (app://) has an
  // opaque "null" origin in a non-standard URL parser, which would make every app://
  // host compare equal. host is parsed correctly for both standard and custom schemes.
  return target.host === renderer.host
}
