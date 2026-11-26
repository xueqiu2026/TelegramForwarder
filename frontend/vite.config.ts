import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/tg/',
  plugins: [react()],
  server: {
    proxy: {
      '/tg/api/ws': { target: 'ws://localhost:8000', ws: true, rewrite: path => path.replace(/^\/tg/, '') },
      '/tg/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: path => path.replace(/^\/tg/, '') },
      '/tg/rss': { target: 'http://localhost:8000', changeOrigin: true, rewrite: path => path.replace(/^\/tg/, '') },
    }
  }
})
