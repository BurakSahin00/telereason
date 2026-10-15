import { Link } from 'react-router'
import { errorSentence, isNotFound, useGate } from '../api/queries.ts'
import type { components } from '../api/schema.d.ts'
import { ChanceScale } from '../components/ChanceScale.tsx'
import { ErrorState, LoadingBlocks } from '../components/LoadStates.tsx'
import { PageHeader } from '../components/PageHeader.tsx'
import { Panel } from '../components/Panel.tsx'
import { StatusChip } from '../components/StatusChip.tsx'
import { VERDICTS } from '../components/verdicts.ts'

function VerdictCard({ line }: { line: components['schemas']['GateLine'] }) {
  return (
    <Panel title={line.line} action={<StatusChip tone={line.verdict}>{VERDICTS[line.verdict].chip}</StatusChip>}>
      <h3 className="text-verdict">{VERDICTS[line.verdict].statement}</h3>
      <p className="mt-3 text-muted">{line.label}</p>
      <p className="mt-5">{line.explanation}</p>
      <div className="mt-6 border-t border-line pt-5">
        <dl className="flex flex-wrap justify-between gap-4">
          <div>
            <dt className="mono-label">AUROC</dt>
            <dd className="mt-2 text-verdict tabular-nums">{line.auroc.toFixed(3)}</dd>
          </div>
          <div className="text-right">
            <dt className="mono-label">Interval</dt>
            <dd className="mt-2 font-mono text-id tabular-nums">
              {line.auroc_ci[0].toFixed(3)} – {line.auroc_ci[1].toFixed(3)}
            </dd>
          </div>
        </dl>
        <ChanceScale auroc={line.auroc} interval={line.auroc_ci} filled={line.verdict !== 'closed'} />
      </div>
    </Panel>
  )
}

function GateBody() {
  const gate = useGate()

  if (gate.isPending) return <LoadingBlocks rows={2} height="h-48" />
  if (gate.isError) {
    return (
      <Panel title="Gate results">
        {isNotFound(gate.error) ? (
          <p>No gate result is stored yet. <Link to="/amd" className="underline underline-offset-4">Start a small night</Link> to check what the data supports.</p>
        ) : (
          <ErrorState message={errorSentence(gate.error, 'the gate')} onRetry={() => void gate.refetch()} />
        )}
      </Panel>
    )
  }

  return (
    <>
      <p className="mono-label mb-4 break-all">Run {gate.data.run_id}</p>
      {gate.data.lines.length === 0 ? (
        <Panel title="No line results"><p className="text-muted">The gate has no lines for this run. Check again after the next night run.</p></Panel>
      ) : (
        <div className="grid gap-gutter lg:grid-cols-2">
          {gate.data.lines.map((line) => <VerdictCard key={line.line} line={line} />)}
        </div>
      )}
      <Panel title="Reading the gate" className="mt-gutter">
        <p>
          AUROC comes from the gate check on held-out data: 0.5 is chance, 1 is perfect. The band is the interval
          around the measured value; when it crosses the dashed chance line, the check is no better than guessing.
        </p>
        <p className="mt-3 text-muted">
          The verdict sets what TeleReason reports on each line: facts only, an unusual-behaviour score, or a diagnosis.
        </p>
      </Panel>
    </>
  )
}

export function GatePage() {
  return (
    <>
      <PageHeader title="Gate" subtitle="What TeleReason can say about each line, and why." />
      <GateBody />
    </>
  )
}
