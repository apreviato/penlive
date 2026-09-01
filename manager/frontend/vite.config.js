import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Dev-only: lets `npm run dev` hit the API on 7777 without CORS friction.
    // In production the API serves dist/ itself, so everything is same-origin.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:7777',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
