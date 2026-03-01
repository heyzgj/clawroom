import { defineConfig } from 'vite';

// In dev, proxy /rooms/* to the local ClawRoom API so:
// 1. CORS is never an issue (same-origin from Vite's perspective)
// 2. SSE EventSource works without credentials
// Set VITE_API_TARGET env var to override (e.g. a staging API)
const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8787';

export default defineConfig({
    server: {
        proxy: {
            '/rooms': {
                target: API_TARGET,
                changeOrigin: true,
            },
            '/join': {
                target: API_TARGET,
                changeOrigin: true,
            },
        },
    },
});
