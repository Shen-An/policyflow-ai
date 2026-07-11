import { apiClient } from '../api/client'
import { queryClient } from '../app/query-client'
import { authStore } from './auth-store'
import { clearReturnTo } from './auth-storage'

let bound = false

export function bindAuthSession(): void {
  if (bound) return
  bound = true
  apiClient.setAccessTokenProvider(() => authStore.getValidAccessToken())
  apiClient.setUnauthorizedHandler(() => {
    authStore.clearSession()
    queryClient.clear()
  })
}

export function logout(storage: Storage | null = typeof window === 'undefined' ? null : window.sessionStorage): void {
  authStore.clearSession()
  clearReturnTo(storage)
  queryClient.clear()
}
