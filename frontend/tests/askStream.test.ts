import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { AskStreamError, readAskStream } from '../src/api/askStream.ts'
import type { AskMessage } from '../src/api/askStream.ts'

const encode = new TextEncoder()
const answer =
  'event: answer\ndata: {"answer":"ANSWERED","answer_source":"model","text":"Türkçe: ölçüm doğru."}\n\n'
const done = 'event: done\ndata: {"total_ms":0,"tokens":0}\n\n'

function stream(value: string, chunkSize = 1) {
  const bytes = encode.encode(value)
  let position = 0
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (position >= bytes.length) {
        controller.close()
        return
      }
      controller.enqueue(bytes.slice(position, position + chunkSize))
      position += chunkSize
    },
  })
}

async function messages(value: string, chunkSize?: number) {
  const result: AskMessage[] = []
  await readAskStream(stream(value, chunkSize), (message) => result.push(message))
  return result
}

test('recorded fixture survives every single-byte transport boundary', async () => {
  const fixture = await readFile(new URL('../../fixtures/ask.sse', import.meta.url), 'utf8')
  const received = await messages(fixture)
  assert.deepEqual(
    received.map((m) => m.event),
    ['tool_call', 'tool_result', 'answer', 'done'],
  )
  assert.equal(received[1].event === 'tool_result' && received[1].data.ms, null)
})

test('split UTF-8 Turkish text and CRLFs decode without corruption', async () => {
  const received = await messages((answer + done).replaceAll('\n', '\r\n'))
  assert.equal(received[0].event === 'answer' && received[0].data.text, 'Türkçe: ölçüm doğru.')
  assert.deepEqual(received[1], { event: 'done', data: { total_ms: 0, tokens: 0 } })
})

test('CR-only separators, comments and unknown future events are handled', async () => {
  const received = await messages(
    (': ping\n\nevent: future\ndata: not-json\n\n' + answer + done).replaceAll('\n', '\r'),
  )
  assert.deepEqual(
    received.map((m) => m.event),
    ['answer', 'done'],
  )
})

test('multi-line data and fields without a space are supported', async () => {
  const value =
    'id: 1\nretry: 1000\nevent:answer\ndata:{"answer":"ANSWERED",\ndata:"answer_source":"tool","text":"Facts only."}\n\n'
  const received = await messages(value + done, 13)
  assert.equal(received[0].event === 'answer' && received[0].data.text, 'Facts only.')
})

test('stream error is terminal even without an answer or done', async () => {
  const received = await messages(
    'event: error\ndata: {"error":{"code":"timeout","message":"Try a narrower question."}}\n\n' +
      answer,
  )
  assert.equal(received.length, 1)
  assert.equal(received[0].event, 'error')
})

test('truncated transport preserves the answer but rejects incomplete completion', async () => {
  const received: AskMessage[] = []
  await assert.rejects(
    readAskStream(stream(answer), (m) => received.push(m)),
    /before the answer was complete/,
  )
  assert.equal(received[0].event, 'answer')
})

test('unterminated final event is not presented as complete', async () => {
  await assert.rejects(messages(answer + done.trimEnd()), /before the answer was complete/)
})

test('done without an answer is rejected', async () => {
  await assert.rejects(messages(done), /without an answer/)
})

test('malformed JSON, missing fields and invalid measurements are rejected', async () => {
  for (const value of [
    'event: answer\ndata: nope\n\n',
    'event: answer\ndata: {"answer":"ANSWERED","text":"Hi"}\n\n',
    'event: tool_call\ndata: {"turn":1,"tool":"get_report","args":null}\n\n',
    'event: tool_result\ndata: {"turn":1,"tool":"get_report","summary":"Hi","ms":-1}\n\n',
    answer + 'event: done\ndata: {"total_ms":1,"tokens":null}\n\n',
  ])
    await assert.rejects(messages(value), AskStreamError)
})

test('oversized unfinished frame is bounded', async () => {
  await assert.rejects(messages('data: ' + 'x'.repeat(262_145), 32_768), /too large/)
})

test('done cancels an otherwise still-open response body', async () => {
  let cancelled = false
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encode.encode(answer + done))
    },
    cancel() {
      cancelled = true
    },
  })
  await readAskStream(body, () => {})
  assert.equal(cancelled, true)
  assert.equal(body.locked, false)
})

test('cancelling a stalled response releases its reader', async () => {
  let cancelled = false
  const controller = new AbortController()
  const body = new ReadableStream<Uint8Array>({
    cancel() {
      cancelled = true
    },
  })
  const pending = readAskStream(body, () => {}, controller.signal)
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })
  assert.equal(cancelled, true)
  assert.equal(body.locked, false)
})
