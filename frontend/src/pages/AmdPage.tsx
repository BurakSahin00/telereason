import { ApiError } from '../api/client.ts'
import { useNightRun, useSystem } from '../api/nightRuns.ts'
import { errorSentence, useGate, useHealth } from '../api/queries.ts'
import { ErrorState, LoadingBlocks } from '../components/LoadStates.tsx'
import { NightSteps } from '../components/NightSteps.tsx'
import { formatSeconds } from '../components/format.ts'
import { PageHeader } from '../components/PageHeader.tsx'
import { Panel } from '../components/Panel.tsx'
import { SegmentMeter } from '../components/SegmentMeter.tsx'
import { StatusChip } from '../components/StatusChip.tsx'
import { VERDICTS } from '../components/verdicts.ts'

function GateStatus() {
  const gate = useGate()
  if (gate.isPending) return <span className="mono-label">Loading gate verdicts…</span>
  if (gate.isError) return <StatusChip tone="fault">Gate unavailable</StatusChip>
  if (gate.data.lines.length === 0) return <StatusChip>No gate results</StatusChip>
  return gate.data.lines.map((line) => (
    <StatusChip key={line.line} tone={line.verdict}>
      {line.line}: {VERDICTS[line.verdict].chip}
    </StatusChip>
  ))
}

function SystemPanels() {
  const system = useSystem()
  const health = useHealth()
  const fake = health.data?.engine === 'fake'
  if (system.isPending) return <LoadingBlocks rows={3} height="h-32" />
  if (system.isError) {
    const message =
      system.error instanceof ApiError && system.error.code === 'not_implemented'
        ? 'System measurements are not available from this backend yet. Try again after the system endpoint is ready.'
        : errorSentence(system.error, 'system measurements')
    return (
      <Panel title="System measurements">
        <ErrorState message={message} onRetry={() => void system.refetch()} />
      </Panel>
    )
  }
  const { gpu, versions } = system.data

  return (
    <>
      <Panel title="GPU and memory">
        {gpu ? (
          <>
            <p className="text-verdict">{gpu.name}</p>
            <p className="mt-4 font-mono text-id tabular-nums">
              {gpu.memory_used_gib.toLocaleString('en-US', { maximumFractionDigits: 2 })} /{' '}
              {gpu.memory_total_gib.toLocaleString('en-US', { maximumFractionDigits: 2 })} GiB used
            </p>
            <SegmentMeter
              value={gpu.memory_total_gib > 0 ? gpu.memory_used_gib / gpu.memory_total_gib : 0}
              className="mt-3"
            />
            <p className="mt-3 text-muted">
              {fake
                ? 'Recorded on the MI300X droplet. This is not a live measurement.'
                : 'GPU memory reported by the backend.'}
            </p>
            <p className="mt-3 text-muted">
              vLLM reserves 85% of GPU memory for its cache, so this is not what the model needs.
            </p>
          </>
        ) : (
          <p className="border border-dotted border-line-strong p-4 text-muted">
            No GPU is visible to this backend. Memory was not measured.
          </p>
        )}
      </Panel>
      <Panel title="Software versions">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 2xl:grid-cols-4">
          {Object.entries(versions).map(([name, version]) => (
            <div key={name} className="min-w-0">
              <dt className="mono-label">{name}</dt>
              <dd
                className={`mt-1 break-all font-mono text-id ${version === null ? 'border border-dotted border-line-strong px-2' : ''}`}
              >
                {version ?? 'Not installed'}
              </dd>
            </div>
          ))}
        </dl>
        {fake && <p className="mt-4 text-muted">Versions recorded on the MI300X droplet.</p>}
      </Panel>
    </>
  )
}

// Own column from SystemPanels (same `system` query, cached): keeps both columns
// closer in height so the page fits one 1080p frame.
function LastNightPanel() {
  const system = useSystem()
  if (system.isPending) return <LoadingBlocks rows={1} height="h-32" />
  if (system.isError) return null
  const { last_night: night } = system.data

  return (
    <Panel title="Last night">
      {night ? (
        <>
          <p className="break-all font-mono text-id text-muted">{night.run_id}</p>
          <p className="mt-3 font-mono text-id tabular-nums">
            {formatSeconds(night.timings_s.total)} total
          </p>
          <NightSteps timings={night.timings_s} status="done" />
          <p className="mt-4 text-muted">Measured step durations from the stored night.</p>
        </>
      ) : (
        <p className="text-muted">No night run is stored yet. Start a small night to see its timings.</p>
      )}
    </Panel>
  )
}

function NightRunPanel() {
  const { start, progress, active, runId } = useNightRun()
  const health = useHealth()
  const fake = health.data?.engine === 'fake'
  const run = progress.data
  const status = run?.status ?? start.data?.status
  const tone =
    status === 'done'
      ? 'open'
      : status === 'failed'
        ? 'fault'
        : status === 'running'
          ? 'screening'
          : 'closed'

  return (
    <Panel title="Run a small night" brackets>
      <p className="text-muted">
        {fake
          ? 'This demo replays a recorded night; it does not start a new scan.'
          : 'Scan up to 300 telemetry windows and follow each step.'}
      </p>
      <button
        type="button"
        disabled={start.isPending || active || !health.data?.ready || health.isError}
        onClick={() => start.mutate()}
        className="mono-label mt-5 border border-line-strong px-4 py-3 text-text hover:bg-raised disabled:cursor-not-allowed disabled:opacity-60"
      >
        {start.isPending
          ? 'Starting…'
          : active
            ? 'Night in progress'
            : fake
              ? 'Preview small night'
              : 'Start small night'}
      </button>
      {health.isPending ? (
        <p className="mt-3 text-muted">Checking the engine…</p>
      ) : health.isError ? (
        <p className="mt-3 text-muted">
          The engine cannot be reached. Check that the backend is running.
        </p>
      ) : !health.data.ready ? (
        <p className="mt-3 text-muted">
          The engine is still loading. You can start a night when it is ready.
        </p>
      ) : null}
      {start.isError && (
        <p role="alert" className="mt-4">
          {nightStartError(start.error)}
        </p>
      )}
      {runId && (
        <div className="mt-6 border-t border-line pt-5">
          <p className="break-all font-mono text-id text-muted">Requested run: {runId}</p>
          {run && run.run_id !== runId && (
            <p className="mt-3 break-all text-muted">
              Recorded result: <span className="font-mono text-id">{run.run_id}</span>
            </p>
          )}
          <div role="status" className="mt-4">
            <StatusChip tone={tone}>{status ?? 'Queued'}</StatusChip>
          </div>
          {progress.isPending && (
            <p className="mt-4 text-muted">Waiting for the first progress update…</p>
          )}
          {progress.isError ? (
            <div className="mt-4">
              <ErrorState
                message={errorSentence(progress.error, 'night progress')}
                onRetry={() => void progress.refetch()}
              />
            </div>
          ) : run ? (
            <>
              <p className="mt-5 font-mono text-id tabular-nums">
                {run.done.toLocaleString('en-US')} / {run.total.toLocaleString('en-US')} windows
              </p>
              {run.total > 0 ? (
                <div
                  className="mt-3"
                  role="progressbar"
                  aria-label="Night run windows"
                  aria-valuemin={0}
                  aria-valuemax={run.total}
                  aria-valuenow={run.done}
                >
                  <SegmentMeter value={run.done / run.total} />
                </div>
              ) : (
                <p className="mt-3 text-muted">
                  {status === 'done'
                    ? 'No windows were processed.'
                    : 'No window total is available yet.'}
                </p>
              )}
              <NightSteps
                timings={run.timings_s}
                status={run.status}
                step={run.step}
                progress={run.total > 0 ? run.done / run.total : undefined}
              />
              {run.status === 'failed' && (
                <p role="alert" className="mt-4">
                  The night run failed. Check the backend before starting another run.
                </p>
              )}
              {run.status === 'done' && (
                <p className="mt-4">
                  {fake ? 'Recorded night preview complete.' : 'Night run complete.'}
                </p>
              )}
            </>
          ) : null}
        </div>
      )}
    </Panel>
  )
}

function nightStartError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return 'A night run is already active. Wait for it to finish, then try again.'
  }
  if (error instanceof ApiError && error.code === 'not_implemented') {
    return 'Night runs are switched off on this server. The video shows a night run on the MI300X.'
  }
  return errorSentence(error, 'the new night run')
}

export function AmdPage() {
  return (
    <>
      <PageHeader
        title="AMD panel"
        subtitle="How the night ran on the system, from measured timings."
        chips={<GateStatus />}
      />
      <div className="grid items-start gap-gutter lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-gutter">
          <SystemPanels />
        </div>
        <div className="flex min-w-0 flex-col gap-gutter">
          <NightRunPanel />
          <LastNightPanel />
        </div>
      </div>
    </>
  )
}
