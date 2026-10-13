// Segments are 3px bars with 4px gaps (DESIGN.md, stat panel and report row).
const segments = (color: string) =>
  `repeating-linear-gradient(90deg, ${color} 0 3px, transparent 3px 7px)`

type SegmentMeterProps = {
  /** Filled share from 0 to 1. */
  value: number
  tone?: 'text' | 'screening'
  className?: string
}

export function SegmentMeter({ value, tone = 'text', className = '' }: SegmentMeterProps) {
  const share = Math.min(Math.max(value, 0), 1)
  return (
    <span
      aria-hidden="true"
      className={`relative block h-4 ${className}`}
      style={{ background: segments('var(--color-line-strong)') }}
    >
      <span
        className="absolute inset-y-0 left-0"
        style={{ width: `${share * 100}%`, background: segments(`var(--color-${tone})`) }}
      />
    </span>
  )
}
