import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Keep proxy to backend and allow serving symlinked /app path
  server: {
    host: true,
    fs: {
      // Allow serving files from both ephemeral workspace root and original read-only app dir
      allow: ['/workspace', '/app']
    },
    proxy: {
      // Proxy all backend API calls through Vite to avoid hardcoding host/port
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
    fs: {
      allow: ['/workspace', '/app'],
    },
  },
})
