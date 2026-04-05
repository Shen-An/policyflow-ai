import { useSyncExternalStore } from 'react'
import type { AuthUser } from '../api/auth'
import { clearStoredSession, readStoredSession, writeStoredSession } from './auth-storage'

export type AuthStatus = 'booting' | 'authenticated' | 'anonymous'
export type AuthState = {
  accessToken: string | null
  expiresAt: number | null
  user: AuthUser | null
  status: AuthStatus
  bootstrapError: Error | null
}

type Listener = () => void

export type AuthStore = ReturnType<typeof createAuthStore>

export function createAuthStore(
  storage: Storage | null = typeof window === 'undefined' ? null : window.sessionStorage,
  now: () => number = Date.now,
) {
  const stored = readStoredSession(storage, now())
  let state: AuthState = stored
    ? { ...stored, user: null, status: 'booting', bootstrapError: null }
    : { accessToken: null, expiresAt: null, user: null, status: 'anonymous', bootstrapError: null }
  const listeners = new Set<Listener>()

  const emit = () => listeners.forEach((listener) => listener())
  const setState = (next: AuthState) => { state = next; emit() }

  return {
    getState: () => state,
    subscribe(listener: Listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    authenticateForDuration(accessToken: string, expiresInSeconds: number, user: AuthUser) {
      const expiresAt = now() + expiresInSeconds * 1000
      writeStoredSession(storage, { accessToken, expiresAt })
      setState({ accessToken, expiresAt, user, status: 'authenticated', bootstrapError: null })
    },
    authenticate(accessToken: string, expiresAt: number, user: AuthUser) {
      writeStoredSession(storage, { accessToken, expiresAt })
      setState({ accessToken, expiresAt, user, status: 'authenticated', bootstrapError: null })
    },
    restoreUser(user: AuthUser) {
      if (!state.accessToken || !state.expiresAt) return
      setState({ ...state, user, status: 'authenticated', bootstrapError: null })
    },
    setBootstrapError(error: Error | null) {
      setState({ ...state, bootstrapError: error })
    },
    clearSession() {
      if (state.status === 'anonymous' && state.accessToken === null) return
      clearStoredSession(storage)
      setState({ accessToken: null, expiresAt: null, user: null, status: 'anonymous', bootstrapError: null })
    },
    getValidAccessToken(): string | null {
      if (!state.accessToken || !state.expiresAt) return null
      if (state.expiresAt <= now()) {
        clearStoredSession(storage)
        setState({ accessToken: null, expiresAt: null, user: null, status: 'anonymous', bootstrapError: null })
        return null
      }
      return state.accessToken
    },
  }
}

export const authStore = createAuthStore()

export function useAuthState<T>(selector: (state: AuthState) => T): T {
  return useSyncExternalStore(authStore.subscribe, () => selector(authStore.getState()), () => selector(authStore.getState()))
}
