import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs on :8000 (uv run uvicorn api.main:app). Calls to /api/* are proxied there;
// set GOPARK_API to point elsewhere. fs.allow lets src/fusionConfig.ts read ../config/fusion.yaml.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    fs: { allow: ['..'] },
    proxy: {
      '/api': {
        target: process.env.GOPARK_API ?? 'http://localhost:8000',
        changeOrigin: true,
        ws: true, // WS /live (useLive with VITE_LIVE_WS=1)
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
