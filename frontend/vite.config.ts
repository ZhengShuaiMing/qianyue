import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发时把 /api 和 /mcp（含模拟面试的 WebSocket）转发给后端，后端不用开跨域。
const backend = process.env.QIANYUE_BACKEND ?? 'http://127.0.0.1:8000'

// npm run build:demo 构建在线演示版：不连后端，数据来自 src/demo/snapshot.json；
// 用相对路径和 # 路由，放在任何静态站点的任意子目录下都能打开
export default defineConfig(({ mode }) => {
  const demo = mode === 'demo'
  return {
    plugins: [react()],
    // 普通构建里 __DEMO__ 是 false，演示相关的代码和快照会被整段去掉
    define: { __DEMO__: JSON.stringify(demo) },
    base: demo ? './' : '/',
    build: demo ? { outDir: 'dist-demo' } : undefined,
    server: {
      port: 5173,
      proxy: {
        '/api': { target: backend, ws: true },
        '/mcp': { target: backend },
      },
    },
  }
})
