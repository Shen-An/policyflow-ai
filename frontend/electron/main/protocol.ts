import { readFile } from 'node:fs/promises'
import path from 'node:path'

import { protocol } from 'electron'

import { buildContentSecurityPolicy } from './hardening'

// The controlled renderer is served over a custom privileged scheme instead of
// file://. A standard, secure scheme gives the renderer a real origin (app://local),
// so a strict `default-src 'self'` CSP resolves correctly for the local bundle —
// which is unreliable under file://. No remote origin is ever involved.

export const APP_SCHEME = 'app'
export const APP_ORIGIN = 'app://local'
export const APP_RENDERER_URL = `${APP_ORIGIN}/index.html`

const CONTENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
}

/** Must be called before `app` is ready. */
export function registerAppProtocolSchemes(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: APP_SCHEME,
      privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true },
    },
  ])
}

/** Serve the built renderer from `rendererRoot`, confined to that directory. */
export function serveRendererOverAppProtocol(rendererRoot: string): void {
  const root = path.resolve(rendererRoot)
  const csp = buildContentSecurityPolicy()
  const htmlHeaders = { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': csp }
  protocol.handle(APP_SCHEME, async (request) => {
    const url = new URL(request.url)
    if (url.host !== 'local') return new Response('Not found', { status: 404 })

    let relative = decodeURIComponent(url.pathname)
    if (!relative || relative === '/') relative = '/index.html'

    const resolved = path.resolve(root, `.${relative}`)
    // Path-traversal guard: never serve outside the renderer root.
    if (resolved !== root && !resolved.startsWith(root + path.sep)) {
      return new Response('Forbidden', { status: 403 })
    }

    try {
      const data = await readFile(resolved)
      const contentType = CONTENT_TYPES[path.extname(resolved).toLowerCase()] ?? 'application/octet-stream'
      const headers: Record<string, string> = { 'Content-Type': contentType }
      // Carry the strict CSP on the document itself (belt-and-suspenders with the
      // index.html meta tag and the session header for http(s) requests).
      if (contentType.startsWith('text/html')) headers['Content-Security-Policy'] = csp
      return new Response(new Uint8Array(data), { status: 200, headers })
    } catch {
      // SPA fallback: unknown client routes resolve to index.html.
      try {
        const html = await readFile(path.join(root, 'index.html'))
        return new Response(new Uint8Array(html), { status: 200, headers: htmlHeaders })
      } catch {
        return new Response('Not found', { status: 404 })
      }
    }
  })
}
