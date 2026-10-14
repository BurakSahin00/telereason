import { Link, useParams } from 'react-router'
import { ApiError } from '../api/client.ts'
import { errorSentence, useWindow } from '../api/queries.ts'
import type { components } from '../api/schema.d.ts'
import { formatScore } from '../components/format.ts'
import { VERDICTS } from '../components/verdicts.ts'
import { ErrorState, LoadingBlocks } from '../components/LoadStates.tsx'
import { PageHeader } from '../components/PageHeader.tsx'
import { Panel } from '../components/Panel.tsx'
import { VerifiedChip } from '../components/ReportRows.tsx'
import { SegmentMeter } from '../components/SegmentMeter.tsx'
import { StatusChip } from '../components/StatusChip.tsx'

type WindowDetail = components['schemas']['WindowDetail']
type Fact = components['schemas']['Fact']

// Same cut-off as the API's plain reading: below 2 standard deviations is within the normal range.
const NORMAL_Z = 2

const SUBTITLES: Record<WindowDetail['verdict'], string> = {
  closed: 'Measured facts only. This line gets no diagnosis.',
  screening: 'The score shows unusual behaviour, not its cause.',
  open: 'Measured facts and the night note for this window.',
}

function Breadcrumb({ windowId, line }: { windowId: string; line?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="mono-label">
      <Link to="/" className="hover:text-text">
        Report
      </Link>
      <span aria-hidden="true"> / </span>
      {line && (
        <>
          <span>{line}</span>
          <span aria-hidden="true"> / </span>
        </>
      )}
      <span className="text-text">{windowId}</span>
    </nav>
  )
}

// The score is shown on a screening line only; a closed line gets no score, meter or cause.
function ScorePanel({ detail }: { detail: WindowDetail }) {
  if (detail.verdict === 'closed') {
    return (
      <Panel title="Screening">
        <p className="text-muted">Not screened on this line (facts only).</p>
      </Panel>
    )
  }
  if (detail.verdict !== 'screening' || detail.score === null) return null

  return (
    <Panel title="Screening" label="Unusual behaviour, not its cause">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <b className="text-stat tabular-nums">{formatScore(detail.score)}</b>
        {detail.flagged === true && <StatusChip tone="screening">Flagged</StatusChip>}
        {detail.flagged === false && <StatusChip>Below the threshold</StatusChip>}
      </div>
      <SegmentMeter value={detail.score} tone="screening" className="mt-4" />
    </Panel>
  )
}

// An unverified note is never shown (docs/api.md); the facts table has the measured values.
function NotePanel({ detail }: { detail: WindowDetail }) {
  return (
    <Panel title="Note">
      {detail.verified ? (
        <p className="text-[19px] leading-[1.5]">{detail.note}</p>
      ) : (
        <p className="text-muted">Facts only. The measured values are in the table below.</p>
      )}
    </Panel>
  )
}

const NUM = 'px-2.5 py-3.5 text-right tabular-nums whitespace-nowrap'
const HEAD = 'px-2.5 pb-3 text-left font-normal'

function FactRow({ fact, lookFirst }: { fact: Fact; lookFirst: boolean }) {
  const calm = Math.abs(fact.z) < NORMAL_Z
  return (
    <tr className={`border-t border-line ${lookFirst ? 'brackets brackets-attention bg-raised' : ''}`}>
      <td className="px-2.5 py-3.5 align-middle">
        {lookFirst && <span className="mono-label block text-screening">Look first</span>}
        <span className="font-mono text-id whitespace-nowrap">{fact.signal}</span>
      </td>
      <td className={NUM}>{fact.window_mean}</td>
      <td className={NUM}>{fact.normal_mean}</td>
      <td className={NUM}>{fact.difference}</td>
      <td className={NUM}>{fact.z}</td>
      <td className={`px-2.5 py-3.5 ${calm ? 'text-muted' : ''}`}>{fact.plain}</td>
    </tr>
  )
}

function FactsPanel({ detail }: { detail: WindowDetail }) {
  const { facts, look_first: lookFirst } = detail
  const lookFirstListed = facts.some((fact) => fact.signal === lookFirst)
  // A high score with every mean in range: say so, without naming any cause.
  const allInRange = facts.length > 0 && facts.every((fact) => Math.abs(fact.z) < NORMAL_Z)

  return (
    <Panel title="Facts" label="Window against normal windows" brackets>
      {facts.length === 0 ? (
        <p className="text-muted">No facts were computed for this window.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-table">
            <thead className="mono-label">
              <tr>
                <th scope="col" className={HEAD}>
                  Signal
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Window mean
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Normal mean
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Difference
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  z
                </th>
                <th scope="col" className={HEAD}>
                  Reading
                </th>
              </tr>
            </thead>
            <tbody>
              {facts.map((fact) => (
                <FactRow key={fact.signal} fact={fact} lookFirst={fact.signal === lookFirst} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!lookFirstListed && (
        <p className="mt-4 text-table text-muted">
          Look first: <span className="font-mono text-id text-text">{lookFirst}</span>
        </p>
      )}
      {detail.flagged === true && allInRange && (
        <p className="mt-4 text-table text-muted">
          Every mean is within the normal range; the score comes from how the signals move together across the
          window, which this table does not show.
        </p>
      )}
    </Panel>
  )
}

function SignalChartPanel() {
  return (
    <Panel title="Signal chart" label="Window against the normal band">
      <p className="text-muted">Signal chart is not available for this window yet.</p>
    </Panel>
  )
}

function WindowBody({ detail }: { detail: WindowDetail }) {
  return (
    <div className="grid gap-gutter xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <div className="flex min-w-0 flex-col gap-gutter">
        <ScorePanel detail={detail} />
        <SignalChartPanel />
      </div>
      <div className="flex min-w-0 flex-col gap-gutter">
        <NotePanel detail={detail} />
        <FactsPanel detail={detail} />
      </div>
    </div>
  )
}

function isMissingWindow(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422)
}

// Layout from design/window.png; the robot arm and the chart come in later issues.
export function WindowPage() {
  const { windowId = '' } = useParams()
  const query = useWindow(windowId)
  const title = `Window ${windowId}`

  if (query.isPending) {
    return (
      <>
        <PageHeader title={title} breadcrumb={<Breadcrumb windowId={windowId} />} />
        <LoadingBlocks rows={4} height="h-24" />
      </>
    )
  }

  if (query.isError) {
    return (
      <>
        <PageHeader title={title} breadcrumb={<Breadcrumb windowId={windowId} />} />
        <Panel>
          {isMissingWindow(query.error) ? (
            <>
              <p>No window {windowId} in the stored run.</p>
              <Link to="/" className="mono-label mt-3 inline-block text-text underline underline-offset-4">
                Back to the report
              </Link>
            </>
          ) : (
            <ErrorState message={errorSentence(query.error, 'this window')} onRetry={() => void query.refetch()} />
          )}
        </Panel>
      </>
    )
  }

  const detail = query.data
  return (
    <>
      <PageHeader
        title={title}
        subtitle={SUBTITLES[detail.verdict]}
        breadcrumb={<Breadcrumb windowId={windowId} line={detail.line} />}
        chips={
          <>
            <StatusChip>{detail.line}</StatusChip>
            <StatusChip tone={detail.verdict}>{VERDICTS[detail.verdict].chip}</StatusChip>
            <VerifiedChip verified={detail.verified} />
          </>
        }
      />
      <WindowBody detail={detail} />
    </>
  )
}
