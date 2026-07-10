import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { AppProviders } from './app/providers'
import { router } from './app/router'
import { env } from './lib/env'
import './styles/index.css'

async function enableDevelopmentMocks(): Promise<void> {
  if (!import.meta.env.DEV || !env.enableMsw) return
  const { worker } = await import('./mocks/browser')
  await worker.start({ onUnhandledRequest: 'bypass' })
}

await enableDevelopmentMocks()

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('Application root element was not found.')

createRoot(rootElement).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
)
