import { Link } from 'react-router'
import type { components } from '../api/schema.d.ts'
import { formatScore } from './format.ts'
import { SegmentMeter } from './SegmentMeter.tsx'

type ReportItem = components['schemas']['ReportItem']

// Shared columns: window id, look-first signal, [score], note, chip.
const SCORE_COLUMNS = 'grid-cols-[100px_220px_200px_1fr_112px]'
const FACTS_COLUMNS = 'grid-cols-[100px_220px_1fr_112px]'

export function VerifiedChip({ verified }: { verified: boolean }) {
  return (
    <span
      className={`mono-label justify-self-end bg-raised px-2.5 py-1 whitespace-nowrap ${
        verified ? 'text-text' : ''
      }`}
    >
      {verified ? 'Verified' : 'Facts only'}
    </span>
  )
}

// An unverified note is never shown (docs/api.md); the window page has the measured facts.
function Note({ item, clamp }: { item: ReportItem; clamp: string }) {
  if (!item.verified) {
    return (
      <span className="text-table text-muted">Facts only. Open the window for the measured values.</span>
    )
  }
  return (
    <span title={item.note} className={`${clamp} text-table`}>
      {item.note}
    </span>
  )
}

type ReportRowsProps = {
  items: ReportItem[]
  /** Score meter column, for lines that have a screening score. Rows without one show a one-line note. */
  withScore: boolean
  /** Screening corner brackets on the first row: the window to look at first. */
  markFirst: boolean
  empty: string
}

export function ReportRows({ items, withScore, markFirst, empty }: ReportRowsProps) {
  if (items.length === 0) return <p className="px-4 py-3.5 text-muted">{empty}</p>

  return (
    <ul>
      {items.map((item, index) => {
        const first = index === 0
        return (
          <li key={item.window_id} className={first ? '' : 'border-t border-line'}>
            <Link
              to={`/windows/${encodeURIComponent(item.window_id)}`}
              aria-label={`Open window ${item.window_id}`}
              className={`grid ${withScore ? SCORE_COLUMNS : FACTS_COLUMNS} items-center gap-5 px-4 py-3.5 hover:bg-raised ${
                first && markFirst ? 'brackets brackets-attention' : ''
              }`}
            >
              <span className="font-mono text-id text-muted">{item.window_id}</span>
              <span className="font-mono text-id [overflow-wrap:anywhere]">{item.look_first}</span>
              {withScore && (
                <span className="flex items-center gap-3.5">
                  <SegmentMeter value={item.score ?? 0} tone="screening" className="flex-1" />
                  <b className="text-[20px] font-semibold tabular-nums">
                    {item.score === null ? '–' : formatScore(item.score)}
                  </b>
                </span>
              )}
              <Note item={item} clamp={withScore ? 'line-clamp-2' : 'line-clamp-1'} />
              <VerifiedChip verified={item.verified} />
            </Link>
          </li>
        )
      })}
    </ul>
  )
}
