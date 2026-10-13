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

export function Panel({ title, label, action, brackets = false, className = '', children }: PanelProps) {
  return (
    <section className={`border border-line bg-panel p-6 ${brackets ? 'brackets' : ''} ${className}`}>
      {(title || label || action) && (
        <div className="mb-4 flex items-baseline justify-between gap-6">
          {title && <h2 className="text-panel-title">{title}</h2>}
          {(label || action) && (
            <div className="flex items-baseline gap-6">
              {label && <span className="mono-label">{label}</span>}
              {action}
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  )
}
