import { useState } from 'react'
import { Link } from 'react-router'
import { errorSentence, isNotFound, useGate, useLatestReport } from '../api/queries.ts'
import type { components } from '../api/schema.d.ts'
import { formatSeconds } from '../components/format.ts'
import { GateLines } from '../components/GateLines.tsx'
import { ErrorState, LoadingBlocks } from '../components/LoadStates.tsx'
import { PageHeader } from '../components/PageHeader.tsx'
import { Panel } from '../components/Panel.tsx'
import { ReportRows } from '../components/ReportRows.tsx'
import { StatPanel } from '../components/StatPanel.tsx'

type ReportResponse = components['schemas']['ReportResponse']
type ReportLine = components['schemas']['ReportLine']

// Rows shown before "Show all", so the page fits one 1080p screen.
const LOOK_FIRST_ROWS = 3
const DEVIATION_ROWS = 2

function GatePanel() {
  const gate = useGate()
  let body
  if (gate.isPending) body = <LoadingBlocks rows={4} height="h-24" />
  else if (gate.isError && isNotFound(gate.error))
    body = <p className="text-muted">No gate result is stored yet. Start a small night from the AMD page.</p>
  else if (gate.isError)
    body = <ErrorState message={errorSentence(gate.error, 'the gate')} onRetry={() => void gate.refetch()} />
  else body = <GateLines lines={gate.data.lines} />

  return (
    <Panel title="Gate" label="What the data can support" brackets className="flex flex-col">
      {body}
    </Panel>
  )
}

// Only the flagged share gets a meter: it is the one stat whose fill carries information.
function FlaggedStat({ report }: { report: ReportResponse }) {
  const screening = report.lines.find((line) => line.verdict === 'screening')
  const flagged = screening?.flagged ?? null

  return (
    <StatPanel
      label="Flagged"
      value={flagged === null ? null : flagged.toLocaleString('en-US')}
      context={
        screening
          ? `of ${screening.scanned.toLocaleString('en-US')} windows on ${screening.line}, ` +
            (screening.threshold === null
              ? 'by screening score'
              : `score ${screening.threshold.toLocaleString('en-US')} or above`)
          : 'No screening line last night'
      }
      meter={flagged !== null && screening?.scanned ? flagged / screening.scanned : 0}
      tone="screening"
    />
  )
}

// A single muted line, not a stat card of its own: scanned windows and the night's
// duration matter but are not the one number worth a meter (#21's "scanned" and "duration").
function NightSummary({ report }: { report: ReportResponse }) {
  const totalScanned = report.lines.reduce((sum, line) => sum + line.scanned, 0)
  const perLine = report.lines
    .map((line) => `${line.line} ${line.scanned.toLocaleString('en-US')}`)
    .join(', ')

  return (
    <p className="mt-3 text-[15px] text-muted">
      {totalScanned.toLocaleString('en-US')} windows scanned last night ({perLine}) in{' '}
      {formatSeconds(report.timings_s.total)}.{' '}
      <Link to="/amd" className="underline underline-offset-4 hover:no-underline">
        Step timings
      </Link>
    </p>
  )
}

type LinePanelProps = {
  title: string
  label: string
  line: ReportLine
  rows: number
  withScore: boolean
  empty: string
}

function LinePanel({ title, label, line, rows, withScore, empty }: LinePanelProps) {
  const [showAll, setShowAll] = useState(false)
  const items = showAll ? line.items : line.items.slice(0, rows)
  const hidden = line.items.length - rows

  return (
    <Panel
      title={title}
      label={label}
      action={
        hidden > 0 && (
          <button
            type="button"
            onClick={() => setShowAll(!showAll)}
            aria-expanded={showAll}
            className="mono-label text-text underline underline-offset-4 hover:no-underline"
          >
            {showAll ? 'Show fewer' : `Show all ${line.items.length}`}
          </button>
        )
      }
    >
      <ReportRows items={items} withScore={withScore} markFirst={withScore} empty={empty} />
    </Panel>
  )
}

function ReportBody() {
  const report = useLatestReport()

  if (report.isPending) {
    return (
      <>
        <div>
          <LoadingBlocks rows={1} height="h-[190px]" />
          <div className="mt-3">
            <LoadingBlocks rows={1} height="h-5" />
          </div>
        </div>
        <Panel title="Look first">
          <LoadingBlocks rows={LOOK_FIRST_ROWS} />
        </Panel>
        <Panel title="Largest deviations">
          <LoadingBlocks rows={DEVIATION_ROWS} />
        </Panel>
      </>
    )
  }

  if (report.isError) {
    return (
      <Panel title="Last night">
        {isNotFound(report.error) ? (
          <p className="text-muted">No night run is stored yet. Start a small night from the AMD page.</p>
        ) : (
          <ErrorState
            message={errorSentence(report.error, "last night's report")}
            onRetry={() => void report.refetch()}
          />
        )}
      </Panel>
    )
  }

  // A closed line never gets a score or a cause: it only lists its largest deviations.
  const scored = report.data.lines.filter((line) => line.verdict !== 'closed')
  const closed = report.data.lines.filter((line) => line.verdict === 'closed')

  return (
    <>
      <div>
        <FlaggedStat report={report.data} />
        <NightSummary report={report.data} />
      </div>
      {scored.map((line) => (
        <LinePanel
          key={line.line}
          title="Look first"
          label={`${line.line}, ranked by score`}
          line={line}
          rows={LOOK_FIRST_ROWS}
          withScore
          empty="No windows were flagged on this line last night."
        />
      ))}
      {closed.map((line) => (
        <LinePanel
          key={line.line}
          title="Largest deviations"
          label={`${line.line}, facts only`}
          line={line}
          rows={DEVIATION_ROWS}
          withScore={false}
          empty="No windows were scanned on this line last night."
        />
      ))}
    </>
  )
}

// Layout from design/report.png: gate on the left, the report on the right.
export function ReportPage() {
  return (
    <>
      <PageHeader title="Morning report" subtitle="What happened last night, and where to look first." />
      <div className="grid gap-gutter lg:grid-cols-[590px_1fr]">
        <GatePanel />
        <div className="flex min-w-0 flex-col gap-gutter">
          <ReportBody />
        </div>
      </div>
    </>
  )
}
