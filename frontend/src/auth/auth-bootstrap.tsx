import { useCallback, useEffect, useState, type PropsWithChildren } from 'react'
import { getCurrentUser } from '../api/auth'
import { AppError } from '../api/errors'
import { FullPageError } from '../components/feedback/full-page-error'
import { FullPageLoading } from '../components/feedback/full-page-loading'
import { authStore, useAuthState } from './auth-store'

export function AuthBootstrap({ children }: PropsWithChildren) {
  const status = useAuthState((state) => state.status)
  const accessToken = useAuthState((state) => state.accessToken)
  const expiresAt = useAuthState((state) => state.expiresAt)
  const bootstrapError = useAuthState((state) => state.bootstrapError)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (status !== 'booting' || !accessToken) return
    const controller = new AbortController()
    let active = true
    authStore.setBootstrapError(null)
    void getCurrentUser(controller.signal)
      .then((user) => { if (active) authStore.restoreUser(user) })
      .catch((error: unknown) => {
        if (!active) return
        if (error instanceof AppError && error.kind === 'auth') {
          authStore.clearSession()
          return
        }
        authStore.setBootstrapError(error instanceof Error ? error : new Error('Unknown bootstrap error'))
      })
    return () => { active = false; controller.abort() }
  }, [accessToken, attempt, status])

  useEffect(() => {
    if (status !== 'authenticated' || !expiresAt) return
    const remaining = expiresAt - Date.now()
    if (remaining <= 0) {
      authStore.clearSession()
      return
    }
    const timer = window.setTimeout(() => authStore.clearSession(), remaining)
    return () => window.clearTimeout(timer)
  }, [expiresAt, status])

  const retry = useCallback(() => {
    authStore.setBootstrapError(null)
    setAttempt((value) => value + 1)
  }, [])

  if (status === 'booting') {
    if (bootstrapError) {
      return <FullPageError message="网络连接失败，无法验证现有会话。请检查网络后重试。" onRetry={retry} />
    }
    return <FullPageLoading />
  }

  return children
}
