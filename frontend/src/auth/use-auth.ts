import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { authStore, useAuthState } from './auth-store'
import { logout as clearAuthSession } from './auth-session'

export function useAuth() {
  const state = useAuthState((current) => current)
  const navigate = useNavigate()
  const logout = useCallback(() => {
    clearAuthSession()
    navigate('/login', { replace: true })
  }, [navigate])
  return { ...state, authenticate: authStore.authenticate, logout }
}
