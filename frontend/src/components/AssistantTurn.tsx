import { Link } from 'react-router'
import type { Conversation } from '../api/useAssistant.ts'
import { answerCodeInfo } from './answerCodes.ts'
import { AssistantAnswer } from './AssistantAnswer.tsx'
import { formatSeconds } from './format.ts'
import { StatusChip } from './StatusChip.tsx'

export function AssistantTurn({
  turn,
  onRetry,
  retryDisabled,
}: {
  turn: Conversation
  onRetry: () => void
  retryDisabled: boolean
}) {
  const unfinished = turn.status === 'waiting' || turn.status === 'running'
  return (
    <article
      className="min-w-0 border-t border-line pt-5 first:border-t-0 first:pt-0"
      aria-label={`Question ${turn.id}`}
    >
      <div className="mb-4 border-l-2 border-line-strong bg-raised p-4">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <span className="mono-label">Your question</span>
          {turn.request.window_id && (
            <Link
              to={`/windows/${turn.request.window_id}`}
              className="font-mono text-id underline underline-offset-4"
            >
              {turn.request.window_id}
            </Link>
          )}
          {turn.fake && <StatusChip>Recorded example</StatusChip>}
        </div>
        <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{turn.request.question}</p>
      </div>
      {turn.steps.length > 0 && (
        <details open={!turn.answer} className="mb-5 border border-line p-4">
          <summary className="mono-label cursor-pointer text-text">
            Tool steps ({turn.steps.length})
          </summary>
          <ol aria-label="Assistant tool steps" className="mt-4 list-decimal space-y-4 pl-6">
            {turn.steps.map((step, index) => (
              <li
                key={`${step.turn}:${step.tool}:${index}`}
                className="pl-1 [overflow-wrap:anywhere]"
              >
                <div className="flex flex-wrap justify-between gap-3">
                  <span className="font-mono text-id">{step.tool}</span>
                  <span
                    className={`font-mono text-id text-muted ${step.result?.ms == null ? 'border border-dotted border-line-strong px-2' : ''}`}
                  >
                    {step.result?.ms == null
                      ? 'Not measured'
                      : formatSeconds(step.result.ms / 1000)}
                  </span>
                </div>
                <p className="mt-1 text-muted">
                  {step.result
                    ? step.result.summary
                    : unfinished
                      ? 'Running…'
                      : 'No result received.'}
                </p>
                <details className="mt-2">
                  <summary className="mono-label cursor-pointer">Arguments</summary>
                  <pre className="mt-2 max-h-48 overflow-auto bg-raised p-3 font-mono text-id">
                    {JSON.stringify(step.args, null, 2)}
                  </pre>
                </details>
              </li>
            ))}
          </ol>
        </details>
      )}
      {turn.answer && (
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <StatusChip>
              {turn.answer.answer_source === 'tool' ? 'From tool' : 'From model'}
            </StatusChip>
            {(() => {
              const code = answerCodeInfo(turn.answer.answer)
              return code && <StatusChip tone={code.tone}>{code.label}</StatusChip>
            })()}
            {turn.done && (
              <span className="font-mono text-id text-muted">
                {formatSeconds(turn.done.total_ms / 1000)} ·{' '}
                {turn.done.tokens.toLocaleString('en-US')} tokens
              </span>
            )}
          </div>
          <AssistantAnswer text={turn.answer.text} />
        </div>
      )}
      {unfinished && (
        <p role="status" className="mt-4 text-muted">
          {turn.answer
            ? 'Finishing the answer…'
            : turn.steps.length > 0
              ? 'Checking the tool results…'
              : turn.request.window_id
                ? 'Reading the window…'
                : 'Reading the question…'}
        </p>
      )}
      {turn.status === 'cancelled' && (
        <p role="status" className="mt-4 text-muted">
          Answer stopped.{turn.answer ? ' The response above may be incomplete.' : ''}
        </p>
      )}
      {turn.error && (
        <div role="alert" className="mt-4 border-l-2 border-fault pl-4">
          {turn.answer && <p className="mb-2">The response above may be incomplete.</p>}
          <p>{turn.error}</p>
          <button
            type="button"
            disabled={retryDisabled}
            onClick={onRetry}
            className="mono-label mt-3 border border-line-strong px-3 py-2 text-text hover:bg-raised disabled:opacity-60"
          >
            Try question again
          </button>
        </div>
      )}
    </article>
  )
}
