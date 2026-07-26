import type { PropsWithChildren } from 'react'
import { ThemeProvider, useTheme } from 'next-themes'
import { App as AntdApp, ConfigProvider, theme as antdThemeAlgo } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useEffect, useMemo, useState } from 'react'
import { registerGlobalMessageApi } from '../app/global-feedback'
import { antdThemeDark, antdThemeLight } from './antd-theme'

export function ThemeRoot({ children }: PropsWithChildren) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem storageKey="policyflow.theme">
      <AntdThemeBridge>{children}</AntdThemeBridge>
    </ThemeProvider>
  )
}

function AntdThemeBridge({ children }: PropsWithChildren) {
  const { resolvedTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const isDark = mounted && resolvedTheme === 'dark'
  const theme = useMemo(
    () => ({
      ...(isDark ? antdThemeDark : antdThemeLight),
      algorithm: isDark ? antdThemeAlgo.darkAlgorithm : antdThemeAlgo.defaultAlgorithm,
    }),
    [isDark],
  )

  return (
    <ConfigProvider locale={zhCN} theme={theme} button={{ autoInsertSpace: false }}>
      <AntdApp>
        <GlobalFeedbackBridge />
        {children}
      </AntdApp>
    </ConfigProvider>
  )
}

function GlobalFeedbackBridge() {
  const { message } = AntdApp.useApp()
  useEffect(() => registerGlobalMessageApi(message), [message])
  return null
}

export function useResolvedColorMode(): 'light' | 'dark' {
  const { resolvedTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  if (!mounted) return 'light'
  return resolvedTheme === 'dark' ? 'dark' : 'light'
}
