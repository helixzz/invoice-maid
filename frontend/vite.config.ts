import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

const backendBaseUrl = process.env.VITE_BACKEND_BASE_URL || 'http://localhost:8000'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: backendBaseUrl,
        changeOrigin: true,
      },
    },
  },
})
