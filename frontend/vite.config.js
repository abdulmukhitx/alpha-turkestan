import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/tiles':    'http://localhost:8000',
      '/api':      'http://localhost:8000',
      '/health':   'http://localhost:8000',
      '/metadata': 'http://localhost:8000',
      '/data':     'http://localhost:8000',
    },
  },
})
