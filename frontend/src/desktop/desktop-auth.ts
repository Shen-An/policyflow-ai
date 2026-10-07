import type { AuthUser, RoleCode } from '../api/auth'
import { desktopApi, isDesktopRuntime } from '../services/desktop-api'
import type { DesktopAuthUser } from '../../electron/shared/types'

// Desktop auth adapter. Inside the Electron shell the renderer has no backend network
// (CSP connect-src 'self'); auth flows through the capability bridge, which holds the
// token in main and returns identity only. We mirror that identity into the shared
// authStore so route guards / the shell work unchanged. The stored "token" is a sentinel
// — the desktop runtime never uses the web apiClient, so no real token lives in the page.

export const DESKTOP_SESSION_SENTINEL = 'desktop-session'

export { isDesktopRuntime }

function toRoleCodes(roles: string[]): RoleCode[] {
  return roles.filter(
    (role): role is RoleCode => role === 'employee' || role === 'kb_admin' || role === 'sys_admin',
  )
}

function toAuthUser(user: DesktopAuthUser): AuthUser {
  return {
    id: user.id,
    username: user.username,
    displayName: user.displayName,
    roles: toRoleCodes(user.roles),
  }
}

export type DesktopSignInResult = { user: AuthUser; expiresAt: number }

export async function signInDesktop(input: {
  username: string
  password: string
}): Promise<DesktopSignInResult> {
  const session = await desktopApi.auth.login(input)
  return { user: toAuthUser(session.user), expiresAt: session.expiresAt }
}

export async function currentUserDesktop(): Promise<AuthUser | null> {
  const user = await desktopApi.auth.currentUser()
  return user ? toAuthUser(user) : null
}

export async function signOutDesktop(): Promise<void> {
  try {
    await desktopApi.auth.logout()
  } catch {
    // Best effort: even if the bridge call fails, the renderer clears local session.
  }
}
