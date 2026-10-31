import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs on :8000 (uv run uvicorn api.main:app). Calls to /api/* are proxied there.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: process.env.GOPARK_API ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
