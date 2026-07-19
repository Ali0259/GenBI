import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    // Only used by `npm run dev`. Forwards /api calls to a locally running
    // backend so the dev server behaves the same as production's same-origin
    // routing without needing an absolute URL baked into the frontend code.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
