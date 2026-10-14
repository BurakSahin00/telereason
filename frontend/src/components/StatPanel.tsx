import { SegmentMeter } from './SegmentMeter.tsx'

type StatPanelProps = {
  label: string
  /** Formatted number, or null when the API did not send one. */
  value: string | null
  unit?: string
  context: string
  /** Meter share from 0 to 1. */
  meter: number
  tone?: 'text' | 'screening'
}

export function StatPanel({ label, value, unit, context, meter, tone = 'text' }: StatPanelProps) {
  // One wide panel: the meter sits beside the number on wider screens and below it on a phone.
  return (
    <section className="grid items-end gap-x-10 gap-y-3.5 border border-line bg-panel px-6.5 py-5.5 md:grid-cols-[auto_1fr]">
      <div>
        <h2 className="mono-label">{label}</h2>
        {value === null ? (
          <p className="mt-3.5 mb-1.5 inline-block border border-dotted border-line-strong px-2 py-3 text-muted">
            Not measured
          </p>
        ) : (
          <p className="mt-3.5 mb-1.5 text-stat tabular-nums">
            {value}
            {unit && <small className="ml-1.5 text-[0.45em] font-medium tracking-normal text-muted">{unit}</small>}
          </p>
        )}
        <p className="text-[15px] text-muted">{context}</p>
      </div>
      <SegmentMeter value={value === null ? 0 : meter} tone={tone} className="md:mb-1" />
    </section>
  )
}
