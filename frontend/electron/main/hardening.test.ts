import { describe, expect, it } from 'vitest'

import {
  HARDENED_WEB_PREFERENCES,
  allowedExternalHosts,
  buildContentSecurityPolicy,
  isExternalLinkAllowed,
  isSameLocalRenderer,
} from './hardening'

describe('HARDENED_WEB_PREFERENCES', () => {
  it('locks the renderer down (isolation + sandbox, no node, no webview)', () => {
    expect(HARDENED_WEB_PREFERENCES.contextIsolation).toBe(true)
    expect(HARDENED_WEB_PREFERENCES.sandbox).toBe(true)
    expect(HARDENED_WEB_PREFERENCES.nodeIntegration).toBe(false)
    expect(HARDENED_WEB_PREFERENCES.nodeIntegrationInWorker).toBe(false)
    expect(HARDENED_WEB_PREFERENCES.nodeIntegrationInSubFrames).toBe(false)
    expect(HARDENED_WEB_PREFERENCES.webviewTag).toBe(false)
    expect(HARDENED_WEB_PREFERENCES.webSecurity).toBe(true)
    expect(HARDENED_WEB_PREFERENCES.allowRunningInsecureContent).toBe(false)
  })

  it('is frozen so the privilege set cannot be mutated at runtime', () => {
    expect(Object.isFrozen(HARDENED_WEB_PREFERENCES)).toBe(true)
  })
})

describe('buildContentSecurityPolicy', () => {
  const csp = buildContentSecurityPolicy()

  it('keeps scripts strict — self only, no inline, no eval', () => {
    expect(csp).toContain("default-src 'self'")
    expect(csp).toContain("script-src 'self'")
    expect(csp).not.toContain("'unsafe-eval'")
    expect(/script-src[^;]*'unsafe-inline'/u.test(csp)).toBe(false)
  })

  it('blocks framing, objects and base/form hijacking, and keeps connect self-only', () => {
    expect(csp).toContain("object-src 'none'")
    expect(csp).toContain("frame-src 'none'")
    expect(csp).toContain("frame-ancestors 'none'")
    expect(csp).toContain("base-uri 'none'")
    expect(csp).toContain("form-action 'none'")
    expect(csp).toContain("connect-src 'self'")
  })
})

describe('isExternalLinkAllowed', () => {
  it('allows only https hosts on the allowlist', () => {
    expect(isExternalLinkAllowed('https://help.policyflow.example/x')).toBe(true)
    expect(isExternalLinkAllowed('https://docs.policyflow.example')).toBe(true)
  })

  it('rejects http, unknown hosts, and non-url input', () => {
    expect(isExternalLinkAllowed('http://help.policyflow.example')).toBe(false)
    expect(isExternalLinkAllowed('https://evil.example')).toBe(false)
    expect(isExternalLinkAllowed('javascript:alert(1)')).toBe(false)
    expect(isExternalLinkAllowed('not a url')).toBe(false)
  })

  it('honours POLICYFLOW_EXTERNAL_HOSTS additions', () => {
    const hosts = allowedExternalHosts({ POLICYFLOW_EXTERNAL_HOSTS: 'portal.corp.example, extra.example' } as NodeJS.ProcessEnv)
    expect(isExternalLinkAllowed('https://portal.corp.example/a', hosts)).toBe(true)
    expect(isExternalLinkAllowed('https://extra.example', hosts)).toBe(true)
    expect(isExternalLinkAllowed('https://evil.example', hosts)).toBe(false)
  })
})

describe('isSameLocalRenderer', () => {
  it('treats the exact app:// renderer as same, foreign origins as different', () => {
    const renderer = 'app://local/index.html'
    expect(isSameLocalRenderer('app://local/index.html', renderer)).toBe(true)
    expect(isSameLocalRenderer('app://local/some/route', renderer)).toBe(true)
    expect(isSameLocalRenderer('https://evil.example/', renderer)).toBe(false)
    expect(isSameLocalRenderer('app://other/index.html', renderer)).toBe(false)
  })

  it('matches a file:// renderer by exact pathname only', () => {
    const renderer = 'file:///app/dist/index.html'
    expect(isSameLocalRenderer('file:///app/dist/index.html', renderer)).toBe(true)
    expect(isSameLocalRenderer('file:///etc/passwd', renderer)).toBe(false)
  })
})
