// Empty VITE_API_URL means same origin: in production the backend serves the
// built frontend, and in development Vite proxies /api to localhost:8000.
const API_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

/** Full URL for an API path such as `/api/health`. */
export function apiUrl(path: string): string {
  return `${API_URL}${path}`
}
