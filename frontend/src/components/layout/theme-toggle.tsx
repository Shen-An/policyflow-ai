import { Moon, Sun } from '@phosphor-icons/react'
import { Button, Tooltip } from 'antd'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const isDark = mounted && resolvedTheme === 'dark'
  const label = isDark ? '切换到浅色模式' : '切换到深色模式'

  return (
    <Tooltip title={label}>
      <Button
        type="text"
        aria-label={label}
        onClick={() => setTheme(isDark ? 'light' : 'dark')}
        icon={
          isDark ? (
            <Sun size={16} weight="duotone" aria-hidden />
          ) : (
            <Moon size={16} weight="duotone" aria-hidden />
          )
        }
      />
    </Tooltip>
  )
}
