import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发态把 /api 代理到本地后端（默认保持 Host 头，与管理端同源校验兼容）；
// 生产构建产物 dist/ 由 FastAPI 同源托管，无需额外配置。
export default defineConfig({
  plugins: [react()],
  // 生产由 FastAPI 在 /admin 下同源托管，资源路径统一带该前缀；开发态同样通过 /admin/ 访问
  base: '/admin/',
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
