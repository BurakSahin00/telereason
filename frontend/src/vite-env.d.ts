/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend base URL. Empty or unset means same origin (the backend serves the built frontend). */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
