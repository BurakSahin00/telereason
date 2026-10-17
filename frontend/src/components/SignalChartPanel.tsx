import { lazy, Suspense, useState } from 'react'
import type { components } from '../api/schema.d.ts'
import { ApiError } from '../api/client.ts'
import { useWindowSignals } from '../api/signals.ts'
import { errorSentence, useHealth } from '../api/queries.ts'
import { SignalDataError } from './signalData.ts'
import { ErrorState, LoadingBlocks } from './LoadStates.tsx'
import { Panel } from './Panel.tsx'
import { StatusChip } from './StatusChip.tsx'

const SignalPlot = lazy(async () => {
  const module = await import('./SignalPlot.tsx')
  return { default: module.SignalPlot }
})

export function SignalChartPanel({ detail }: { detail: components['schemas']['WindowDetail'] }) {
  const channels = [
    ...new Set([detail.look_first, ...detail.facts.map((fact) => fact.signal)].filter(Boolean)),
  ]
  const [selected, setSelected] = useState(channels[0] ?? '')
  const signals = useWindowSignals(detail.window_id, selected)
  const health = useHealth()
  const fake = health.data?.engine === 'fake'
  const missing =
    signals.error instanceof ApiError &&
    [404, 501, 503].includes(signals.error.status) &&
    ['not_found', 'not_implemented', 'engine_not_ready'].includes(signals.error.code ?? '')
  return (
    <Panel title="Signal chart">
      {channels.length === 0 ? (
        <p className="text-muted">No signals were identified for this window.</p>
      ) : (
        <>
          <label htmlFor={`signal-${detail.window_id}`} className="mono-label mb-2 block">
            Signal
          </label>
          <select
            id={`signal-${detail.window_id}`}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            className="w-full min-w-0 border border-line-strong bg-bg px-3 py-3 font-mono text-id"
          >
            {channels.map((name) => (
              <option key={name} value={name}>
                {name}
                {name === detail.look_first ? ' · Look first' : ''}
              </option>
            ))}
          </select>
          {signals.isPending ? (
            <div className="mt-5">
              <LoadingBlocks rows={1} height="h-[320px]" />
            </div>
          ) : signals.isError && missing && fake ? (
            <p className="mt-5 text-muted">
              The recorded demo does not include the time series. The video shows this chart on the
              MI300X.
            </p>
          ) : signals.isError ? (
            <div className="mt-5">
              <ErrorState
                message={
                  missing
                    ? 'The time series and normal band are not available for this signal yet. You can still read the measured facts.'
                    : signals.error instanceof SignalDataError
                      ? signals.error.message
                      : errorSentence(signals.error, 'the signal chart')
                }
                onRetry={() => void signals.refetch()}
              />
            </div>
          ) : signals.data === null ? (
            <p className="mt-5 text-muted">
              No samples with a normal band were returned for this signal. Select another signal or
              read the facts.
            </p>
          ) : signals.data ? (
            <>
              <Suspense
                fallback={
                  <div className="mt-5">
                    <LoadingBlocks rows={1} height="h-[320px]" />
                  </div>
                }
              >
                <SignalPlot series={signals.data} name={selected} />
              </Suspense>
              <ul
                aria-label="Signal chart legend"
                className="mt-4 flex flex-wrap gap-x-5 gap-y-3 font-mono text-label text-muted"
              >
                <li className="flex items-center gap-2">
                  <span aria-hidden="true" className="w-5 border-t-2 border-text" />
                  Signal
                </li>
                <li className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-3 w-5 border border-dashed border-line-strong bg-raised"
                  />
                  Normal band (low–high)
                </li>
                <li className="flex items-center gap-2">
                  <span aria-hidden="true" className="w-5 border-t-2 border-screening" />
                  Outside normal range
                </li>
              </ul>
              <div className="mt-4 flex flex-wrap gap-3">
                <StatusChip tone={signals.data.outsideSamples > 0 ? 'screening' : 'closed'}>
                  {signals.data.outsideSamples.toLocaleString('en-US')} /{' '}
                  {signals.data.points.length.toLocaleString('en-US')} samples outside
                </StatusChip>
              </div>
              <p className="mt-3 text-muted">
                Measured samples against their normal range. Hover or use the arrow keys to inspect
                values.
              </p>
              {signals.data.points.length === 1 && (
                <p className="mt-2 text-muted">
                  Only one sample is available; there is no time trend to show.
                </p>
              )}
            </>
          ) : null}
        </>
      )}
    </Panel>
  )
}
