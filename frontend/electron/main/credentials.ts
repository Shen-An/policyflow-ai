import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs'

/** The subset of Electron's `safeStorage` the vault needs (OS-backed encryption). */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean
  encryptString(plainText: string): Buffer
  decryptString(encrypted: Buffer): string
}

/** Encrypted-blob persistence (a single file under the app's userData directory). */
export interface VaultFileStore {
  read(): Buffer | null
  write(data: Buffer): void
  remove(): void
}

/**
 * Holds the desktop session's credentials in the main process only.
 *
 * - The long-lived secret (a refresh token when the backend issues one) is encrypted
 *   with the OS keychain via `safeStorage` and written to disk — never in plaintext,
 *   never handed to the renderer. No IPC operation returns it.
 * - The short-lived access token lives in memory only and is injected onto proxied
 *   requests by the API proxy; it is never persisted and never returned to the
 *   renderer either (the renderer receives identity + expiry, not the bearer).
 *
 * NOTE: the current backend issues only an access token (no dedicated refresh
 * endpoint). The vault is designed for refresh-token rotation; until the backend
 * provides one, the persisted slot carries whatever session secret login yields, so
 * the security property — secret only in main, encrypted at rest — holds today.
 */
export class CredentialVault {
  private accessToken: string | null = null
  private accessTokenExpiresAt = 0

  constructor(
    private readonly crypto: SafeStorageLike,
    private readonly store: VaultFileStore,
  ) {}

  /** Encrypt and persist the long-lived session secret. */
  saveRefreshToken(token: string): void {
    if (!this.crypto.isEncryptionAvailable()) {
      throw new Error('OS secure storage is unavailable; refusing to persist credentials.')
    }
    this.store.write(this.crypto.encryptString(token))
  }

  /** Decrypt the persisted session secret, or null if absent/undecryptable. */
  loadRefreshToken(): string | null {
    const data = this.store.read()
    if (!data || !this.crypto.isEncryptionAvailable()) return null
    try {
      return this.crypto.decryptString(data)
    } catch {
      return null
    }
  }

  hasRefreshToken(): boolean {
    return this.store.read() != null
  }

  setAccessToken(token: string, expiresAt: number): void {
    this.accessToken = token
    this.accessTokenExpiresAt = expiresAt
  }

  /** The in-memory access token, or null if unset/expired. Main-only. */
  getAccessToken(now: number = Date.now()): string | null {
    if (!this.accessToken) return null
    return now < this.accessTokenExpiresAt ? this.accessToken : null
  }

  getExpiresAt(): number {
    return this.accessTokenExpiresAt
  }

  clearAccessToken(): void {
    this.accessToken = null
    this.accessTokenExpiresAt = 0
  }

  /** Forget everything: in-memory token and the encrypted file at rest. */
  clear(): void {
    this.clearAccessToken()
    this.store.remove()
  }
}

/** File-backed store writing a single encrypted blob with owner-only permissions. */
export function createFileVaultStore(filePath: string): VaultFileStore {
  return {
    read() {
      try {
        return existsSync(filePath) ? readFileSync(filePath) : null
      } catch {
        return null
      }
    },
    write(data) {
      writeFileSync(filePath, data, { mode: 0o600 })
    },
    remove() {
      try {
        if (existsSync(filePath)) rmSync(filePath)
      } catch {
        // Best-effort cleanup; absence is the desired post-condition.
      }
    },
  }
}

/** Wire the vault to Electron's `safeStorage` and a userData file (called from main). */
export function createCredentialVault(crypto: SafeStorageLike, filePath: string): CredentialVault {
  return new CredentialVault(crypto, createFileVaultStore(filePath))
}
