import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Deploying to the domain root (vibe.elivcloud.org/), so asset URLs in
  // dist/index.html must be absolute from "/", not relative.
  base: '/',
  build: {
    outDir: 'dist',
  },
  // Dev-only: lets `npm run dev` reach a locally-running FastAPI backend at
  // the same relative /api path the client already uses (client.ts assumes
  // same-origin; in production Nginx is what proxies /api/, not this).
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
