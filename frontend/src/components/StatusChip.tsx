import type { ReactNode } from 'react'

/** Meaning colours from DESIGN.md; a chip without a tone is a plain muted label. */
export type StatusTone = 'closed' | 'screening' | 'open' | 'fault'

const DOT_CLASS: Record<StatusTone, string> = {
  closed: 'bg-closed',
  screening: 'bg-screening',
  open: 'bg-open',
  fault: 'bg-fault',
}

type StatusChipProps = {
  tone?: StatusTone
  children: ReactNode
}

// The word always carries the state; the dot only marks it.
export function StatusChip({ tone, children }: StatusChipProps) {
  return (
    <span
      className={`mono-label inline-flex items-center gap-2 bg-raised px-2.5 py-1 whitespace-nowrap ${
        tone ? 'text-text' : 'text-muted'
      }`}
    >
      {tone && <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${DOT_CLASS[tone]}`} />}
      {children}
    </span>
  )
}
