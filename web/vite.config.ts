import { defineConfig } from 'vite';
const target = 'http://127.0.0.1:8765';
export default defineConfig({
  base: './',
  server: {
    host: '0.0.0.0', port: 5173,
    // /data/<file> is first served from web/public/data (prep output); /data/grid/* and anything
    // missing locally goes to the FastAPI server.
    proxy: {
      '/api': { target, changeOrigin: true },
      '/runs': { target, changeOrigin: true },
      '/data/grid': { target, changeOrigin: true },
    },
  },
  build: { target: 'es2022', chunkSizeWarningLimit: 2000 },
});
