import { useEffect, useRef, useState } from 'react'
import type { components } from './schema.d.ts'
import { ask } from './ask.ts'
import { ApiError } from './client.ts'
import { AskStreamError } from './askStream.ts'
import type { AskMessage } from './askStream.ts'
import { errorSentence } from './queries.ts'

export type ToolStep = components['schemas']['ToolCallEvent'] & {
  result?: components['schemas']['ToolResultEvent']
}
export type Conversation = {
  id: number
  request: components['schemas']['AskRequest']
  fake: boolean
  status: 'waiting' | 'running' | 'done' | 'error' | 'cancelled'
  steps: ToolStep[]
  answer?: components['schemas']['AnswerEvent']
  done?: components['schemas']['DoneEvent']
  error?: string
}

function receive(turn: Conversation, message: AskMessage): Conversation {
  switch (message.event) {
    case 'tool_call':
      return { ...turn, status: 'running', steps: [...turn.steps, message.data] }
    case 'tool_result': {
      const { data } = message
      const found = turn.steps.some((step) => step.turn === data.turn && step.tool === data.tool)
      const steps = found
        ? turn.steps.map((step) =>
            step.turn === data.turn && step.tool === data.tool ? { ...step, result: data } : step,
          )
        : [...turn.steps, { turn: data.turn, tool: data.tool, args: {}, result: data }]
      return { ...turn, status: 'running', steps }
    }
    case 'answer':
      return { ...turn, status: 'running', answer: message.data }
    case 'done':
      return { ...turn, status: 'done', done: message.data }
    case 'error':
      return {
        ...turn,
        status: 'error',
        error: errorSentence(
          new ApiError(200, message.data.error.code, message.data.error.message),
          'the answer',
        ),
      }
  }
}

export function useAssistant() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const controller = useRef<AbortController | null>(null)
  const nextId = useRef(0)
  useEffect(
    () => () => {
      controller.current?.abort()
    },
    [],
  )

  function update(id: number, change: (turn: Conversation) => Conversation) {
    setConversations((turns) => turns.map((turn) => (turn.id === id ? change(turn) : turn)))
  }

  async function send(request: Conversation['request'], fake: boolean) {
    if (controller.current) return
    const current = new AbortController()
    controller.current = current
    const id = ++nextId.current
    setConversations((turns) => [...turns, { id, request, fake, status: 'waiting', steps: [] }])
    try {
      await ask(
        request,
        (message) => {
          if (!current.signal.aborted) update(id, (turn) => receive(turn, message))
        },
        current.signal,
      )
    } catch (error) {
      if (!current.signal.aborted) {
        update(id, (turn) => ({
          ...turn,
          status: 'error',
          error:
            error instanceof AskStreamError ? error.message : errorSentence(error, 'the answer'),
        }))
      }
    } finally {
      if (controller.current === current) controller.current = null
    }
  }

  function cancel() {
    controller.current?.abort()
    controller.current = null
    setConversations((turns) =>
      turns.map((turn) =>
        turn.status === 'waiting' || turn.status === 'running'
          ? { ...turn, status: 'cancelled' }
          : turn,
      ),
    )
  }

  const active = conversations.some(
    (turn) => turn.status === 'waiting' || turn.status === 'running',
  )
  return { conversations, active, send, cancel }
}
