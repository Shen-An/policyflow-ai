import type { PropsWithChildren } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import type { RoleCode } from '../api/auth'
import { useAuthState } from './auth-store'
import { readReturnTo, saveReturnTo } from './auth-storage'
import { hasAnyRole } from './permissions'

export function ProtectedRoute({ children }: PropsWithChildren) {
  const status = useAuthState((state) => state.status)
  const location = useLocation()
  if (status !== 'authenticated') {
    saveReturnTo(window.sessionStorage, `${location.pathname}${location.search}${location.hash}`)
    return <Navigate to="/login" replace />
  }
  return children
}

export function PublicOnlyRoute({ children }: PropsWithChildren) {
  const status = useAuthState((state) => state.status)
  return status === 'authenticated'
    ? <Navigate to={readReturnTo(window.sessionStorage) ?? '/'} replace />
    : children
}

export function RoleGuard({ required, children }: PropsWithChildren<{ required: RoleCode[] }>) {
  const user = useAuthState((state) => state.user)
  return user && hasAnyRole(user.roles, required) ? children : <Navigate to="/forbidden" replace />
}
