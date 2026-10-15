import type { ReactNode } from 'react'

type PanelProps = {
  title?: string
  /** Mono label on the right of the title row. */
  label?: string
  /** Control at the right end of the title row, after the label. */
  action?: ReactNode
  /** Neutral corner brackets: only for the one panel that matters on the screen. */
  brackets?: boolean
  className?: string
  children?: ReactNode
}

export function Panel({
  title,
  label,
  action,
  brackets = false,
  className = '',
  children,
}: PanelProps) {
  return (
    <section
      className={`min-w-0 border border-line bg-panel p-6 ${brackets ? 'brackets' : ''} ${className}`}
    >
      {(title || label || action) && (
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-3">
          {title && <h2 className="text-panel-title">{title}</h2>}
          {(label || action) && (
            <div className="flex min-w-0 max-w-full flex-wrap items-baseline gap-x-6 gap-y-3">
              {label && <span className="mono-label [overflow-wrap:anywhere]">{label}</span>}
              {action}
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  )
}
