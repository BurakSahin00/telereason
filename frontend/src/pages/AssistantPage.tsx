import { useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { errorSentence, useExamples, useHealth } from '../api/queries.ts'
import { useAssistant } from '../api/useAssistant.ts'
import { AssistantTurn } from '../components/AssistantTurn.tsx'
import { ErrorState, LoadingBlocks } from '../components/LoadStates.tsx'
import { PageHeader } from '../components/PageHeader.tsx'
import { Panel } from '../components/Panel.tsx'

const FIELD =
  'w-full min-w-0 border border-line-strong bg-bg px-3 py-3 text-body disabled:opacity-60'
const BUTTON =
  'mono-label border border-line-strong px-4 py-3 text-text hover:bg-raised disabled:cursor-not-allowed disabled:opacity-60'

function ExampleQuestions({
  onChoose,
  disabled,
}: {
  onChoose: (question: string) => void
  disabled: boolean
}) {
  const examples = useExamples()
  if (examples.isPending) return <LoadingBlocks rows={2} height="h-16" />
  if (examples.isError)
    return (
      <ErrorState
        message={errorSentence(examples.error, 'example questions')}
        onRetry={() => void examples.refetch()}
      />
    )
  if (examples.data.examples.length === 0)
    return <p className="text-muted">No example questions yet. Write your own question.</p>
  return (
    <ul className="space-y-3">
      {examples.data.examples.map((example, index) => (
        <li key={`${example.lang}:${index}`}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChoose(example.text)}
            className="w-full border border-line-strong p-4 text-left hover:bg-raised disabled:opacity-60"
          >
            <span className="mono-label mb-2 block">
              {example.lang === 'tr' ? 'Türkçe' : 'English'}
            </span>
            <span lang={example.lang} className="[overflow-wrap:anywhere]">
              {example.text}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

export function AssistantPage() {
  const [params] = useSearchParams()
  const initialWindow = params.get('window_id') ?? ''
  const [windowId, setWindowId] = useState(initialWindow)
  const [question, setQuestion] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)
  const health = useHealth()
  const { conversations, active, send, cancel } = useAssistant()
  const unavailable = !health.data?.ready || health.isError
  const fake = health.data?.engine === 'fake'
  return (
    <>
      <PageHeader
        title="Assistant"
        subtitle="Ask about a window, a line or a maintenance protocol."
      />
      <div className="grid items-start gap-gutter xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-gutter">
          <Panel title="Example questions">
            <p className="mb-4 text-muted">Choose a question to edit before asking.</p>
            <ExampleQuestions
              disabled={active}
              onChoose={(text) => {
                setQuestion(text)
                input.current?.focus()
              }}
            />
          </Panel>
          <Panel title="Ask a question">
            <form
              onSubmit={(event) => {
                event.preventDefault()
                if (active || unavailable || !question.trim()) return
                void send(
                  {
                    question: question.trim(),
                    ...(windowId.trim() ? { window_id: windowId.trim() } : {}),
                  },
                  fake,
                )
                setQuestion('')
              }}
            >
              <label htmlFor="assistant-window" className="mono-label mb-2 block">
                Window ID (optional)
              </label>
              <input
                id="assistant-window"
                value={windowId}
                onChange={(event) => setWindowId(event.target.value)}
                disabled={active}
                pattern="W-[0-9]{6}"
                placeholder="W-246062"
                aria-describedby="assistant-window-help"
                className={`${FIELD} font-mono text-id`}
              />
              <p id="assistant-window-help" className="mt-2 text-muted">
                Leave empty to ask about the lines or a protocol.
              </p>
              <label htmlFor="assistant-question" className="mono-label mt-5 mb-2 block">
                Question
              </label>
              <textarea
                ref={input}
                id="assistant-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                disabled={active}
                required
                rows={3}
                className={`${FIELD} resize-y`}
                placeholder="What did last night's scan find?"
              />
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="submit"
                  disabled={active || unavailable || !question.trim()}
                  className={BUTTON}
                >
                  {active ? 'Answer in progress' : 'Ask'}
                </button>
                {active && (
                  <button type="button" onClick={cancel} className={BUTTON}>
                    Stop answer
                  </button>
                )}
              </div>
              {health.isPending ? (
                <p role="status" className="mt-3 text-muted">
                  Checking the engine…
                </p>
              ) : health.isError ? (
                <div className="mt-4">
                  <ErrorState
                    message={errorSentence(health.error, 'engine status')}
                    onRetry={() => void health.refetch()}
                  />
                </div>
              ) : !health.data.ready ? (
                <p role="status" className="mt-3 text-muted">
                  The engine is still loading. You can ask when it is ready.
                </p>
              ) : null}
            </form>
          </Panel>
        </div>
        <div className="flex min-w-0 flex-col gap-gutter">
          <Panel title="Conversation" brackets>
            {fake && (
              <p className="mb-5 border border-dotted border-line-strong p-3 text-muted">
                This demo replays one recorded answer for every question. Live answers run on the
                MI300X; the video shows them.
              </p>
            )}
            {conversations.length === 0 ? (
              <p className="text-muted">Ask a question to see the tool steps and answer here.</p>
            ) : (
              <div className="space-y-6">
                {conversations.map((turn) => (
                  <AssistantTurn
                    key={turn.id}
                    turn={turn}
                    retryDisabled={active || unavailable}
                    onRetry={() => void send(turn.request, fake)}
                  />
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </>
  )
}
