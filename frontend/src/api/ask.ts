import type { components } from './schema.d.ts'
import { ApiError, apiUrl, toApiError } from './client.ts'
import { AskStreamError, readAskStream } from './askStream.ts'
import type { AskMessage } from './askStream.ts'

export async function ask(
  body: components['schemas']['AskRequest'],
  onMessage: (message: AskMessage) => void,
  signal: AbortSignal,
): Promise<void> {
  // An idle proxy must not leave the screen waiting forever. The server timeout is 60 s.
  const timeout = AbortSignal.timeout(75_000)
  const requestSignal = AbortSignal.any([signal, timeout])
  try {
    const response = await fetch(apiUrl('/api/ask'), {
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: requestSignal,
    })
    if (!response.ok) throw await toApiError(response)
    if (!response.body || !response.headers.get('content-type')?.startsWith('text/event-stream')) {
      throw new AskStreamError(
        'The server did not return an assistant stream. Check the backend, then try again.',
      )
    }
    await readAskStream(response.body, onMessage, requestSignal)
  } catch (error) {
    if (signal.aborted) throw error
    if (timeout.aborted)
      throw new ApiError(0, 'timeout', 'The answer took too long. Try a narrower question.')
    if (error instanceof ApiError || error instanceof AskStreamError) throw error
    throw new ApiError(
      0,
      null,
      'The connection to the assistant was lost. Check the backend, then try again.',
    )
  }
}
