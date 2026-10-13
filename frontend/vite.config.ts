import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend runs on :8000 in development (ENGINE=fake). In production it
// serves the built frontend from the same origin, so no proxy is needed there.
const apiProxy = { '/api': 'http://localhost:8000' }

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
})
