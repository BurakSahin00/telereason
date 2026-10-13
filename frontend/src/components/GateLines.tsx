import type { components } from '../api/schema.d.ts'
import { ChanceScale } from './ChanceScale.tsx'
import { StatusChip } from './StatusChip.tsx'

type Verdict = components['schemas']['GateLine']['verdict']
type GateLine = components['schemas']['GateLine']

/** Chip word, verdict statement and legend text per gate verdict (DESIGN.md, Gate verdict). */
const VERDICTS: Record<Verdict, { chip: string; statement: string; legend: string }> = {
  closed: {
    chip: 'Facts only',
    statement: 'No diagnosis on this line',
    legend: 'Measured facts, no diagnosis',
  },
  screening: {
    chip: 'Screening',
    statement: 'Unusual behaviour only',
    legend: 'Unusual behaviour score, no cause',
  },
  open: { chip: 'Diagnosis', statement: 'Diagnosis on this line', legend: 'Fault names allowed' },
}

const LEGEND_ORDER: Verdict[] = ['closed', 'screening', 'open']

function GateLineView({ line }: { line: GateLine }) {
  const verdict = VERDICTS[line.verdict]
  return (
    <div className="border-line py-5.5 first:pt-0 not-first:border-t">
      <div className="flex items-center justify-between gap-4">
        <span className="font-mono text-id tracking-[0.06em] text-muted uppercase">{line.line}</span>
        <StatusChip tone={line.verdict}>{verdict.chip}</StatusChip>
      </div>
      <p className="mt-3 mb-2 text-verdict">{verdict.statement}</p>
      <p className="text-table text-muted">{line.explanation}</p>
      <ChanceScale auroc={line.auroc} interval={line.auroc_ci} filled={line.verdict !== 'closed'} />
    </div>
  )
}

function StatusLegend() {
  return (
    <div className="mt-auto border-t border-line pt-4.5">
      <p className="mono-label mb-3">Status legend</p>
      <ul>
        {LEGEND_ORDER.map((v) => (
          <li key={v} className="grid grid-cols-[150px_1fr] items-center py-1 text-[15px] text-muted">
            <StatusChip tone={v} bare>
              {VERDICTS[v].chip}
            </StatusChip>
            <span>{VERDICTS[v].legend}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function GateLines({ lines }: { lines: GateLine[] }) {
  return (
    <>
      {lines.length === 0 ? (
        <p className="text-muted">The gate has no lines for this run.</p>
      ) : (
        <div>
          {lines.map((line) => (
            <GateLineView key={line.line} line={line} />
          ))}
        </div>
      )}
      <StatusLegend />
    </>
  )
}
