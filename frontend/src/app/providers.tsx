import type { PropsWithChildren } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { AuthBootstrap } from '../auth/auth-bootstrap'
import { bindAuthSession } from '../auth/auth-session'
import { antdTheme } from '../styles/antd-theme'
import { queryClient } from './query-client'

bindAuthSession()

export function AppProviders({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={antdTheme}>
        <AntdApp>
          <AuthBootstrap>{children}</AuthBootstrap>
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
