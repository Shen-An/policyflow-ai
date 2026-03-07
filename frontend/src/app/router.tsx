import { createBrowserRouter } from 'react-router-dom'
import { App } from './App'
import { FoundationPage } from './foundation-page'
import { NotFoundPage } from './not-found-page'

export const router = createBrowserRouter([
  {
    element: <App />,
    children: [
      { path: '/', element: <FoundationPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
