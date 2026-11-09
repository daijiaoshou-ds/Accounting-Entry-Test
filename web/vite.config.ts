import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发：Vite 5173，/api 代理到本机 FastAPI 8502（省掉 CORS 与硬编码域名）
// 生产：npm run build 产出 web/dist，由 api/app.py 直接挂载在同一个 8502 上
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8502', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
