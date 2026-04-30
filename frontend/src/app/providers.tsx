import type { PropsWithChildren } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { AuthBootstrap } from '../auth/auth-bootstrap'
import { bindAuthSession } from '../auth/auth-session'
import { queryClient } from './query-client'

bindAuthSession()

const theme = {
  token: {
    colorPrimary: '#4f46e5',
    colorInfo: '#4f46e5',
    colorSuccess: '#15803d',
    colorWarning: '#b45309',
    colorError: '#dc2626',
    colorLink: '#4f46e5',
    borderRadius: 8,
    motion: false,
    fontFamily:
      '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    colorBgLayout: '#f5f7fb',
    colorBorder: '#e3e8f0',
    colorText: '#0f1729',
    colorTextSecondary: '#5b6577',
  },
  components: {
    Layout: {
      siderBg: '#0b1220',
      headerBg: '#ffffff',
      bodyBg: '#f5f7fb',
      triggerBg: '#131c30',
    },
    Menu: {
      darkItemBg: '#0b1220',
      darkSubMenuItemBg: '#0b1220',
      darkItemSelectedBg: '#4f46e5',
      darkItemHoverBg: '#1a2438',
      itemBorderRadius: 8,
    },
    Card: {
      borderRadiusLG: 12,
    },
    Button: {
      controlHeight: 36,
      borderRadius: 8,
    },
    Input: {
      controlHeight: 36,
      borderRadius: 8,
    },
    Select: {
      controlHeight: 36,
      borderRadius: 8,
    },
    Table: {
      headerBg: '#f8fafc',
      rowHoverBg: '#eef2ff',
    },
  },
} as const

export function AppProviders({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={theme} autoInsertSpaceInButton={false}>
        <AntdApp>
          <AuthBootstrap>{children}</AuthBootstrap>
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
