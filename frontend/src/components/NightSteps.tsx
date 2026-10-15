import type { components } from '../api/schema.d.ts'
import { formatSeconds } from './format.ts'
import { SegmentMeter } from './SegmentMeter.tsx'

type Progress = components['schemas']['NightRunProgress']

const STEPS = [
  ['gate', 'Gate'],
  ['facts', 'Facts'],
  ['scan', 'Notes and verifier'],
  ['report', 'Report'],
] as const

export function NightSteps({
  timings,
  status,
  step,
  progress,
}: {
  timings: Progress['timings_s']
  status: Progress['status']
  step?: Progress['step']
  progress?: number
}) {
  const currentIndex = STEPS.findIndex(([name]) => name === step)
  return (
    <ol aria-label="Night run steps" className="mt-5">
      {STEPS.map(([key, label], index) => {
        const failed = status === 'failed' && step === key
        const running = status === 'running' && step === key
        const measured = timings[key] !== null && timings[key] !== undefined
        const completed =
          status === 'done' || measured || (status !== 'queued' && index < currentIndex)
        const state = failed
          ? 'Failed'
          : running
            ? 'Running'
            : completed
              ? 'Done'
              : status === 'failed'
                ? 'Not run'
                : 'Waiting'
        const mark = failed
          ? 'bg-fault'
          : running
            ? 'bg-screening'
            : completed
              ? 'bg-open'
              : 'border border-line-strong'
        return (
          <li
            key={key}
            className="relative pl-6 pb-4 last:pb-0"
            aria-current={running ? 'step' : undefined}
          >
            <span
              aria-hidden="true"
              className={`absolute top-2 left-0 size-2.5 ${failed ? '' : 'rounded-full'} ${mark}`}
            />
            {index < STEPS.length - 1 && (
              <span
                aria-hidden="true"
                className="absolute top-5 bottom-0 left-1 border-l border-dotted border-line-strong"
              />
            )}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{label}</span>
              <span className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
                <span className="mono-label">{state}</span>
                <span
                  className={`font-mono text-id tabular-nums ${measured ? '' : 'border border-dotted border-line-strong px-2'}`}
                >
                  {measured ? formatSeconds(timings[key]!) : 'Not measured'}
                </span>
              </span>
            </div>
            {running && progress !== undefined && (
              <SegmentMeter value={progress} tone="screening" className="mt-3" />
            )}
          </li>
        )
      })}
    </ol>
  )
}
