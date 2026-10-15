import { createBrowserRouter } from 'react-router'
import { AppLayout } from './layouts/AppLayout.tsx'
import { LoadingBlocks } from './components/LoadStates.tsx'
import { AmdPage } from './pages/AmdPage.tsx'
import { GatePage } from './pages/GatePage.tsx'
import { NotFoundPage } from './pages/NotFoundPage.tsx'
import { ReportPage } from './pages/ReportPage.tsx'
import { WindowPage } from './pages/WindowPage.tsx'

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    hydrateFallbackElement: (
      <div role="status" className="mx-auto max-w-content px-page pt-6">
        <p className="mono-label mb-6">Loading the page…</p>
        <LoadingBlocks rows={4} height="h-24" />
      </div>
    ),
    children: [
      { index: true, element: <ReportPage /> },
      { path: 'windows/:windowId', element: <WindowPage /> },
      { path: 'gate', element: <GatePage /> },
      {
        path: 'assistant',
        lazy: async () => {
          const { AssistantPage } = await import('./pages/AssistantPage.tsx')
          return { Component: AssistantPage }
        },
      },
      { path: 'amd', element: <AmdPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
