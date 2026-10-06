import { describe, expect, it } from 'vitest'

import { CredentialVault, type SafeStorageLike, type VaultFileStore } from './credentials'

function fakeCrypto(available = true): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plain) => Buffer.from(`ENC:${Buffer.from(plain, 'utf8').toString('base64')}`, 'utf8'),
    decryptString: (buffer) => Buffer.from(buffer.toString('utf8').slice(4), 'base64').toString('utf8'),
  }
}

function memoryStore(): { store: VaultFileStore; peek: () => Buffer | null } {
  let data: Buffer | null = null
  return {
    store: {
      read: () => data,
      write: (value) => {
        data = value
      },
      remove: () => {
        data = null
      },
    },
    peek: () => data,
  }
}

describe('CredentialVault', () => {
  it('persists the refresh token encrypted and never in plaintext at rest', () => {
    const { store, peek } = memoryStore()
    const vault = new CredentialVault(fakeCrypto(), store)
    vault.saveRefreshToken('refresh-secret-xyz')

    const atRest = peek()?.toString('utf8') ?? ''
    expect(atRest).not.toContain('refresh-secret-xyz')
    expect(vault.loadRefreshToken()).toBe('refresh-secret-xyz')
    expect(vault.hasRefreshToken()).toBe(true)
  })

  it('refuses to persist when OS encryption is unavailable', () => {
    const { store } = memoryStore()
    const vault = new CredentialVault(fakeCrypto(false), store)
    expect(() => vault.saveRefreshToken('x')).toThrow()
  })

  it('keeps the access token in memory and expires it', () => {
    const { store } = memoryStore()
    const vault = new CredentialVault(fakeCrypto(), store)
    const now = 1_000_000
    vault.setAccessToken('access-abc', now + 1000)
    expect(vault.getAccessToken(now)).toBe('access-abc')
    expect(vault.getAccessToken(now + 2000)).toBe(null)
  })

  it('clears both the in-memory token and the encrypted file', () => {
    const { store, peek } = memoryStore()
    const vault = new CredentialVault(fakeCrypto(), store)
    vault.setAccessToken('access-abc', Date.now() + 10_000)
    vault.saveRefreshToken('refresh-secret-xyz')
    vault.clear()
    expect(vault.getAccessToken()).toBe(null)
    expect(peek()).toBe(null)
    expect(vault.hasRefreshToken()).toBe(false)
  })
})
