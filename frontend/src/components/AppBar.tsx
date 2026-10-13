import { Link, useLocation } from 'react-router'
import { EngineStatus } from './EngineStatus.tsx'
import { LastRun } from './LastRun.tsx'

type NavItem = {
  to: string
  label: string
  isActive: (pathname: string) => boolean
}

// The window page sits one level below the report, so Report stays underlined there.
const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Report', isActive: (p) => p === '/' || p.startsWith('/windows/') },
  { to: '/gate', label: 'Gate', isActive: (p) => p.startsWith('/gate') },
  { to: '/assistant', label: 'Assistant', isActive: (p) => p.startsWith('/assistant') },
  { to: '/amd', label: 'AMD', isActive: (p) => p.startsWith('/amd') },
]

export function AppBar() {
  const { pathname } = useLocation()

  return (
    <header className="brackets mt-6 grid h-[76px] grid-cols-[1fr_auto_1fr] items-center gap-6 border border-line px-7">
      <Link to="/" className="justify-self-start text-panel-title font-bold">
        TeleReason
      </Link>

      <nav aria-label="Main" className="flex gap-9">
        {NAV_ITEMS.map((item) => {
          const active = item.isActive(pathname)
          return (
            <Link
              key={item.to}
              to={item.to}
              aria-current={active ? 'page' : undefined}
              className={`border-b-[1.5px] px-0.5 py-1.5 font-mono text-id tracking-[0.08em] uppercase ${
                active ? 'border-text text-text' : 'border-transparent text-muted hover:text-text'
              }`}
            >
              {item.label}
            </Link>
          )
        })}
      </nav>

      <div className="flex items-center justify-self-end gap-7">
        <LastRun />
        <EngineStatus />
      </div>
    </header>
  )
}
