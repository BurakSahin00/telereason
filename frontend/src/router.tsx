import { createBrowserRouter } from 'react-router'
import { AppLayout } from './layouts/AppLayout.tsx'
import { AmdPage } from './pages/AmdPage.tsx'
import { AssistantPage } from './pages/AssistantPage.tsx'
import { GatePage } from './pages/GatePage.tsx'
import { NotFoundPage } from './pages/NotFoundPage.tsx'
import { ReportPage } from './pages/ReportPage.tsx'
import { WindowPage } from './pages/WindowPage.tsx'

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <ReportPage /> },
      { path: 'windows/:windowId', element: <WindowPage /> },
      { path: 'gate', element: <GatePage /> },
      { path: 'assistant', element: <AssistantPage /> },
      { path: 'amd', element: <AmdPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
