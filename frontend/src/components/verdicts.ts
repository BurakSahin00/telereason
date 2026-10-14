import type { components } from '../api/schema.d.ts'

export type Verdict = components['schemas']['GateLine']['verdict']

/** Chip word, verdict statement and legend text per gate verdict (DESIGN.md, Gate verdict). */
export const VERDICTS: Record<Verdict, { chip: string; statement: string; legend: string }> = {
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
