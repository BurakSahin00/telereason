import { Outlet } from 'react-router'
import { AppBar } from '../components/AppBar.tsx'

export function AppLayout() {
  return (
    <div className="px-page pb-page">
      <div className="mx-auto max-w-content">
        <AppBar />
        <main>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
