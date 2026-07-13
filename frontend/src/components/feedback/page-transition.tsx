import type { PropsWithChildren } from 'react'
import { useLocation } from 'react-router-dom'

export function PageTransition({ children }: PropsWithChildren) {
  const location = useLocation()
  return (
    <div key={location.pathname} className="page-enter">
      {children}
    </div>
  )
}
