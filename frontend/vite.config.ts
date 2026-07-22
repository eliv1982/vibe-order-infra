import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Deploying to the domain root (vibe.elivcloud.org/), so asset URLs in
  // dist/index.html must be absolute from "/", not relative.
  base: '/',
  build: {
    outDir: 'dist',
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
