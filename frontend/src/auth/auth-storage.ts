export const AUTH_SESSION_KEY = 'policyflow.auth.session'
export const AUTH_RETURN_TO_KEY = 'policyflow.auth.return-to'

type StoredSession = { accessToken: string; expiresAt: number }

export function readStoredSession(storage: Storage | null, now = Date.now()): StoredSession | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(AUTH_SESSION_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<StoredSession>
    if (typeof parsed.accessToken !== 'string' || typeof parsed.expiresAt !== 'number' || parsed.expiresAt <= now) {
      storage.removeItem(AUTH_SESSION_KEY)
      return null
    }
    return { accessToken: parsed.accessToken, expiresAt: parsed.expiresAt }
  } catch {
    storage.removeItem(AUTH_SESSION_KEY)
    return null
  }
}

export function writeStoredSession(storage: Storage | null, session: StoredSession): void {
  storage?.setItem(AUTH_SESSION_KEY, JSON.stringify(session))
}

export function clearStoredSession(storage: Storage | null): void {
  storage?.removeItem(AUTH_SESSION_KEY)
}

export function saveReturnTo(storage: Storage | null, target: string): void {
  if (!storage || !target.startsWith('/') || target.startsWith('//') || target.startsWith('/login')) return
  storage.setItem(AUTH_RETURN_TO_KEY, target)
}

export function readReturnTo(storage: Storage | null): string | null {
  if (!storage) return null
  const target = storage.getItem(AUTH_RETURN_TO_KEY)
  return target && target.startsWith('/') && !target.startsWith('//') ? target : null
}

export function consumeReturnTo(storage: Storage | null): string | null {
  const target = readReturnTo(storage)
  storage?.removeItem(AUTH_RETURN_TO_KEY)
  return target
}

export function clearReturnTo(storage: Storage | null): void {
  storage?.removeItem(AUTH_RETURN_TO_KEY)
}
