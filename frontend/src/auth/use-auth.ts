import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { isDesktopRuntime, signOutDesktop } from '../desktop/desktop-auth'
import { authStore, useAuthState } from './auth-store'
import { logout as clearAuthSession } from './auth-session'

export function useAuth() {
  const state = useAuthState((current) => current)
  const navigate = useNavigate()
  const logout = useCallback(() => {
    if (isDesktopRuntime()) void signOutDesktop()
    clearAuthSession()
    navigate('/login', { replace: true })
  }, [navigate])
  return { ...state, authenticate: authStore.authenticate, logout }
}
