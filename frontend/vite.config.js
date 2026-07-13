import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    headers: {
      // Required by Google Identity Services when testing over HTTP on localhost.
      'Referrer-Policy': 'no-referrer-when-downgrade',
      'Cross-Origin-Opener-Policy': 'same-origin-allow-popups',
    },
    proxy: {
      '/tiles':    'http://localhost:8000',
      '/api':      'http://localhost:8000',
      '/health':   'http://localhost:8000',
      '/metadata': 'http://localhost:8000',
      '/data':     'http://localhost:8000',
    },
  },
})
