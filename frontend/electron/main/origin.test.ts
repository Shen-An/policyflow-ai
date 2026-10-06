import { describe, expect, it } from 'vitest'

import { isTrustedSenderUrl } from './origin'

const origin = { rendererUrl: 'app://local/index.html', devServerUrl: null as string | null }

describe('isTrustedSenderUrl', () => {
  it('trusts the controlled app:// renderer and its client routes', () => {
    expect(isTrustedSenderUrl('app://local/index.html', origin)).toBe(true)
    expect(isTrustedSenderUrl('app://local/workspace', origin)).toBe(true)
  })

  it('rejects foreign origins, data/blob frames and empty senders', () => {
    expect(isTrustedSenderUrl('https://evil.example/', origin)).toBe(false)
    expect(isTrustedSenderUrl('app://other/index.html', origin)).toBe(false)
    expect(isTrustedSenderUrl('data:text/html,<script>1</script>', origin)).toBe(false)
    expect(isTrustedSenderUrl('about:blank', origin)).toBe(false)
    expect(isTrustedSenderUrl(undefined, origin)).toBe(false)
    expect(isTrustedSenderUrl('', origin)).toBe(false)
  })

  it('trusts the dev server origin only when one is configured', () => {
    const dev = { rendererUrl: 'http://127.0.0.1:5173/', devServerUrl: 'http://127.0.0.1:5173' }
    expect(isTrustedSenderUrl('http://127.0.0.1:5173/chat', dev)).toBe(true)
    expect(isTrustedSenderUrl('http://127.0.0.1:5173/chat', origin)).toBe(false)
  })
})
