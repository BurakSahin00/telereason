import type { components, paths } from './schema.d.ts'

// Empty VITE_API_URL means same origin: in production the backend serves the
// built frontend, and in development Vite proxies /api to localhost:8000.
const API_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

/** Full URL for an API path such as `/api/health`. */
export function apiUrl(path: string): string {
  return `${API_URL}${path}`
}

type ErrorResponse = components['schemas']['ErrorResponse']
type ErrorCode = components['schemas']['ErrorDetail']['code']

type GetOperation<P extends keyof paths> = paths[P] extends { get: infer Op } ? Op : never
type JsonOk<Op> = Op extends { responses: { 200: { content: { 'application/json': infer T } } } }
  ? T
  : never

/** API paths that answer GET with JSON, as written in the OpenAPI document. */
export type GetPath = {
  [P in keyof paths]: [JsonOk<GetOperation<P>>] extends [never] ? never : P
}[keyof paths]

/** The 200 body of a GET path, generated from the backend schema. */
export type GetResponse<P extends GetPath> = JsonOk<GetOperation<P>>

type GetParams<P extends GetPath> =
  GetOperation<P> extends { parameters: infer Params } ? Params : never

type GetOptions<P extends GetPath> = Omit<GetParams<P>, 'header' | 'cookie'> & {
  signal?: AbortSignal
}

// The options argument is required only when the path has {placeholders} to fill.
type GetArgs<P extends GetPath> =
  GetParams<P> extends { path: object } ? [options: GetOptions<P>] : [options?: GetOptions<P>]

/** A failed API call. `code` is the API's error code, or null when no API error body came back. */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode | null

  constructor(status: number, code: ErrorCode | null, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function isErrorResponse(body: unknown): body is ErrorResponse {
  if (typeof body !== 'object' || body === null || !('error' in body)) return false
  const { error } = body
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    'message' in error &&
    typeof error.code === 'string' &&
    typeof error.message === 'string'
  )
}

export async function toApiError(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null)
  if (isErrorResponse(body)) {
    return new ApiError(response.status, body.error.code, body.error.message)
  }
  return new ApiError(response.status, null, `The server answered with HTTP ${response.status}.`)
}

type UrlParams = { path?: object; query?: object; signal?: AbortSignal }

function buildUrl(path: string, options?: UrlParams): string {
  const pathParams = (options?.path ?? {}) as Record<string, unknown>
  const filled = path.replace(/\{(\w+)\}/g, (_, name: string) =>
    encodeURIComponent(String(pathParams[name])),
  )
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(options?.query ?? {})) {
    if (value !== undefined && value !== null) search.set(key, String(value))
  }
  const query = search.toString()
  return apiUrl(query ? `${filled}?${query}` : filled)
}

/**
 * GET a JSON endpoint. The path, its parameters and the return type come from
 * the generated schema; a failure throws ApiError with the API's message.
 */
export async function getJson<P extends GetPath>(
  path: P,
  ...[options]: GetArgs<P>
): Promise<GetResponse<P>> {
  let response: Response
  try {
    response = await fetch(buildUrl(path, options as UrlParams | undefined), {
      headers: { Accept: 'application/json' },
      signal: options?.signal,
    })
  } catch (error) {
    if (options?.signal?.aborted) throw error
    throw new ApiError(0, null, "Can't reach the server.")
  }
  if (!response.ok) throw await toApiError(response)
  return (await response.json()) as GetResponse<P>
}

/** Start a night run using the request and accepted response from OpenAPI. */
export async function startNightRun(
  body: components['schemas']['NightRunRequest'],
): Promise<components['schemas']['NightRunStarted']> {
  let response: Response
  try {
    response = await fetch(apiUrl('/api/night-runs'), {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, null, "Can't reach the server.")
  }
  if (!response.ok) throw await toApiError(response)
  return (await response.json()) as components['schemas']['NightRunStarted']
}
