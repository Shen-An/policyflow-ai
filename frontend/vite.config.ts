import { createReadStream, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

const projectRoot = fileURLToPath(new URL('.', import.meta.url))
const backendProxy = {
  '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
  '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
}

function serveMockWorkerInDevelopment(): Plugin {
  return {
    name: 'serve-msw-worker-in-development',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url !== '/mockServiceWorker.js') {
          next()
          return
        }
        const workerPath = path.resolve(projectRoot, '.msw/mockServiceWorker.js')
        if (!existsSync(workerPath)) {
          response.statusCode = 404
          response.end('MSW worker is not initialized. Run npm run mock:init.')
          return
        }
        response.setHeader('Content-Type', 'application/javascript; charset=utf-8')
        response.setHeader('Cache-Control', 'no-store')
        createReadStream(workerPath).pipe(response)
      })
    },
  }
}

// The strict CSP meta ships in index.html for the production (web + Electron app://)
// renderer. On the dev server it must be removed: Vite's React Fast Refresh injects an
// inline module script that `script-src 'self'` would block, breaking HMR.
function stripCspMetaInDevelopment(): Plugin {
  return {
    name: 'strip-csp-meta-in-dev',
    apply: 'serve',
    transformIndexHtml(html) {
      return html.replace(/\s*<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?\/>/iu, '')
    },
  }
}

export default defineConfig({
  // Relative base so the built renderer loads correctly when served over the
  // Electron app:// scheme (and still works when served at a web root).
  base: './',
  plugins: [react(), tailwindcss(), serveMockWorkerInDevelopment(), stripCspMetaInDevelopment()],
  resolve: {
    alias: {
      '@': path.resolve(projectRoot, 'src'),
    },
  },
  server: {
    host: '127.0.0.1',
    // Prefer PORT from the preview harness / host env; fall back to 5173.
    port: Number(process.env.PORT || 5173),
    strictPort: false,
    proxy: backendProxy,
  },
  preview: {
    host: '127.0.0.1',
    port: Number(process.env.PORT || 4173),
    strictPort: false,
    proxy: backendProxy,
  },
  build: {
    manifest: true,
    sourcemap: false,
  },
})
