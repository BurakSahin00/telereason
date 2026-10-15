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
    <header className="brackets grid min-h-[76px] grid-cols-1 items-center gap-x-6 gap-y-4 border border-line px-4 py-4 sm:grid-cols-[auto_minmax(0,1fr)] sm:px-6 xl:grid-cols-[auto_minmax(0,1fr)_auto] xl:px-7 2xl:grid-cols-[1fr_auto_1fr]">
      <Link to="/" className="justify-self-start text-panel-title font-bold">
        TeleReason
      </Link>

      <nav
        aria-label="Main"
        className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 sm:col-span-2 sm:row-start-2 sm:justify-self-center sm:gap-x-6 xl:col-span-1 xl:col-start-2 xl:row-start-1 2xl:gap-x-9"
      >
        {NAV_ITEMS.map((item) => {
          const active = item.isActive(pathname)
          return (
            <Link
              key={item.to}
              to={item.to}
              aria-current={active ? 'page' : undefined}
              className={`border-b-[1.5px] px-0.5 py-1.5 font-mono text-label tracking-[0.08em] uppercase sm:text-id ${
                active ? 'border-text text-text' : 'border-transparent text-muted hover:text-text'
              }`}
            >
              {item.label}
            </Link>
          )
        })}
      </nav>

      <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3 sm:col-start-2 sm:row-start-1 sm:justify-self-end xl:col-start-3 2xl:gap-x-7">
        <LastRun />
        <EngineStatus />
      </div>
    </header>
  )
}
