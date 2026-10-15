import type { paths } from './schema.d.ts'

export type AskMessage = paths['/api/ask']['post']['responses'][200]['content']['text/event-stream']

export class AskStreamError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AskStreamError'
  }
}

const EVENTS = new Set(['tool_call', 'tool_result', 'answer', 'done', 'error'])
const ERROR_CODES = new Set([
  'bad_request',
  'not_found',
  'busy',
  'rate_limited',
  'engine_not_ready',
  'not_implemented',
  'timeout',
  'internal_error',
])
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const count = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 0
const text = (value: unknown) => typeof value === 'string' && value.trim().length > 0

function parseMessage(event: string, raw: string): AskMessage | undefined {
  if (!EVENTS.has(event)) return undefined
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    throw new AskStreamError('The assistant sent an unreadable response. Try the question again.')
  }
  let valid = false
  if (object(data)) {
    switch (event) {
      case 'tool_call':
        valid = count(data.turn) && data.turn !== 0 && text(data.tool) && object(data.args)
        break
      case 'tool_result':
        valid =
          count(data.turn) &&
          data.turn !== 0 &&
          text(data.tool) &&
          typeof data.summary === 'string' &&
          (data.ms === null || count(data.ms))
        break
      case 'answer':
        valid =
          text(data.answer) &&
          text(data.text) &&
          (data.answer_source === 'tool' || data.answer_source === 'model')
        break
      case 'done':
        valid = count(data.total_ms) && count(data.tokens)
        break
      case 'error':
        valid =
          object(data.error) &&
          typeof data.error.code === 'string' &&
          ERROR_CODES.has(data.error.code) &&
          text(data.error.message)
        break
    }
  }
  if (!valid)
    throw new AskStreamError('The assistant sent an invalid response. Try the question again.')
  return { event, data } as AskMessage
}

/** Decode SSE across arbitrary UTF-8 chunks, including split CRLFs and ping comments. */
export async function readAskStream(
  stream: ReadableStream<Uint8Array>,
  onMessage: (message: AskMessage) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let pending = '',
    event = '',
    data: string[] = [],
    frameSize = 0
  let answered = false,
    ended = false
  const abort = () => {
    void reader.cancel().catch(() => {})
  }
  signal?.addEventListener('abort', abort, { once: true })
  function line(value: string) {
    if (value === '') {
      if (data.length > 0) {
        const message = parseMessage(event, data.join('\n'))
        if (message) {
          if (message.event === 'done' && !answered)
            throw new AskStreamError(
              'The assistant finished without an answer. Try the question again.',
            )
          if (message.event === 'answer') answered = true
          if (message.event === 'done' || message.event === 'error') ended = true
          onMessage(message)
        }
      }
      event = ''
      data = []
      frameSize = 0
      return
    }
    if (value.startsWith(':')) return
    frameSize += value.length
    if (frameSize > 262_144)
      throw new AskStreamError('The assistant response was too large. Try a narrower question.')
    const colon = value.indexOf(':')
    const field = colon < 0 ? value : value.slice(0, colon)
    let content = colon < 0 ? '' : value.slice(colon + 1)
    if (content.startsWith(' ')) content = content.slice(1)
    if (field === 'event') event = content
    if (field === 'data') data.push(content)
  }
  try {
    while (!ended) {
      signal?.throwIfAborted()
      const chunk = await reader.read()
      signal?.throwIfAborted()
      pending += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true })
      while (!ended) {
        const end = pending.search(/[\r\n]/)
        if (end < 0 || (pending[end] === '\r' && end === pending.length - 1 && !chunk.done)) break
        const size = pending[end] === '\r' && pending[end + 1] === '\n' ? 2 : 1
        const value = pending.slice(0, end)
        pending = pending.slice(end + size)
        line(value)
      }
      if (pending.length + frameSize > 262_144)
        throw new AskStreamError('The assistant response was too large. Try a narrower question.')
      if (chunk.done) break
    }
    if (!ended)
      throw new AskStreamError(
        'The connection ended before the answer was complete. Try the question again.',
      )
  } finally {
    signal?.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
