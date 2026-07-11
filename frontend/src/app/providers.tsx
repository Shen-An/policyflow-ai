import type { PropsWithChildren } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { AuthBootstrap } from '../auth/auth-bootstrap'
import { bindAuthSession } from '../auth/auth-session'
import { queryClient } from './query-client'

bindAuthSession()

export function AppProviders({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}><AuthBootstrap>{children}</AuthBootstrap></QueryClientProvider>
}
