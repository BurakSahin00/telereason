import { useQuery } from '@tanstack/react-query'
import { ApiError, getJson } from './client.ts'

// Shared query hooks, so the app bar and the report page read one cached answer.

export function useLatestReport() {
  return useQuery({
    queryKey: ['report', 'latest'],
    queryFn: ({ signal }) => getJson('/api/report/latest', { signal }),
  })
}

export function useGate() {
  return useQuery({
    queryKey: ['gate'],
    queryFn: ({ signal }) => getJson('/api/gate', { signal }),
  })
}

/** True when the API answered that nothing is stored yet, which is an empty state, not an error. */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'not_found'
}

// No answer at all, or a proxy (Vite in development, the tunnel in the demo) with no backend behind it.
const UNREACHABLE_STATUS = new Set([0, 502, 503, 504])

/** One sentence for an error state: what happened and what to do. */
export function errorSentence(error: unknown, what: string): string {
  if (error instanceof ApiError && error.code === null && UNREACHABLE_STATUS.has(error.status)) {
    return `Can't reach the server, so ${what} can't be shown. Check that the backend is running, then try again.`
  }
  if (error instanceof ApiError) return `${error.message} Try again in a moment.`
  return `Something went wrong while loading ${what}. Try again in a moment.`
}
