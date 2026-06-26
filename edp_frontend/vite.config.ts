import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Proxy: todas as chamadas REST e WS passam pelo backend EDP em :8000
// Nenhuma configuração de CORS é necessária no backend (já tem allow_origins=["*"])
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // WebSocket
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
      // REST — todos os paths usados pelo EDP
      '/health':              { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/connect':             { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/chat':                { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/memory':              { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/dashboard':           { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/metrics':             { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/snapshot':            { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/providers':           { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/tools':               { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/mode':                { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/lineage':             { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/cognitive_decisions': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/flags':               { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
