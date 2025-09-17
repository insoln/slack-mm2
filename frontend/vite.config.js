import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Allow Vite dev server to serve files that are symlinked outside the root (/workspace) to read-only /app
  server: {
    fs: {
      allow: ['/workspace', '/app'],
    },
  },
})
