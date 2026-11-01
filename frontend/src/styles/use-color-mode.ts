/**
 * Theme helpers that stay render-safe.
 *
 * next-themes resolves the system/class theme only after the first client render, so
 * theme-dependent UI has to know whether it is still on the very first paint. The
 * `useSyncExternalStore` subscription does that without scheduling a state update from
 * an effect (which react-hooks/set-state-in-effect flags as a cascading render).
 */
import { useSyncExternalStore } from 'react'
import { useTheme } from 'next-themes'

const emptySubscribe = () => () => {}

export function useHasMounted(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  )
}

export function useResolvedColorMode(): 'light' | 'dark' {
  const { resolvedTheme } = useTheme()
  const mounted = useHasMounted()
  if (!mounted) return 'light'
  return resolvedTheme === 'dark' ? 'dark' : 'light'
}
