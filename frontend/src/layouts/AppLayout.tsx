import { Outlet } from 'react-router'
import { AppBar } from '../components/AppBar.tsx'

export function AppLayout() {
  return (
    <div className="px-4 pt-4 pb-6 sm:px-6 sm:pt-6 sm:pb-page xl:px-page">
      <div className="mx-auto max-w-content">
        <AppBar />
        <main className="min-w-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
